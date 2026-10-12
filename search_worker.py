"""Bounded host search over validated regular-file descriptors."""
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import time


def main():
    q = json.loads(sys.argv[1])
    workspace = Path(q['workspace']).resolve()
    denied = [Path(p).resolve() for p in q.get('denied_paths', [])]
    root = Path(q['path'])
    rg = shutil.which('rg')
    if not rg:
        return {'error': 'ripgrep is required for bounded search'}
    deadline = time.monotonic() + 10
    reasons, items, seen, size = set(), [], 0, 0
    processes = []

    def allowed(path):
        path = Path(path).resolve()
        return path.is_relative_to(workspace) and not any(path.is_relative_to(p) for p in denied)

    def records(process, delimiter):
        pending = b''
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(max(0, remaining)):
                    reasons.add('time_limit'); return
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    return
                pending += chunk
                if len(pending) > 2_000_000:
                    reasons.add('oversized_record'); return
                while delimiter in pending:
                    line, pending = pending.split(delimiter, 1)
                    yield line

    def spawn(args, fds=()):
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, pass_fds=tuple(fds))
        processes.append(process)
        return process

    def candidates():
        if root.is_file():
            yield os.fsencode(root)
            return
        args = [rg, '--files', '--hidden', '--null', '--sort', 'path',
                '--glob', '!.git/**', '--glob', '!**/.git/**',
                '--glob', '!**/node_modules/**', '--glob', '!**/.venv/**',
                '--glob', '!**/__pycache__/**']
        if q['glob']:
            args += ['--glob', q['glob']]
        # Exclusions are after the caller's glob. Descriptor checks below also
        # cover aliases, parent symlinks and concurrent replacement of a path.
        for path in denied:
            if path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                # Avoid treating policy names as caller-provided glob syntax.
                escaped = ''.join('\\' + c if c in '*?[]{}!\\' else c for c in relative)
                args += ['--glob', '!' + escaped, '--glob', '!' + escaped + '/**']
        process = spawn([*args, '--', str(root)])
        yield from records(process, b'\0')
        code = process.wait(timeout=max(.1, deadline - time.monotonic()))
        if code not in (0, 1):
            reasons.add('unreadable_paths')

    def scan(batch):
        nonlocal seen, size
        descriptors, names = [], {}
        try:
            for raw in batch:
                path = Path(os.fsdecode(raw))
                if not allowed(path):
                    continue
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                except OSError:
                    reasons.add('unreadable_paths'); continue
                # Check the file actually opened BEFORE rg reads its contents.
                actual = Path(f'/proc/self/fd/{fd}').resolve()
                if not stat.S_ISREG(os.fstat(fd).st_mode) or not allowed(actual):
                    os.close(fd); continue
                descriptors.append(fd)
                names[f'/proc/self/fd/{fd}'] = str(actual.relative_to(workspace))
            if not descriptors:
                return False
            args = [rg, '--json', '--line-number']
            if q['literal']:
                args.append('--fixed-strings')
            process = spawn([*args, '--', q['query'], *names], descriptors)
            for raw in records(process, b'\n'):
                event = json.loads(raw)
                if event.get('type') != 'match':
                    continue
                data = event['data']
                path, text = data['path'].get('text'), data['lines'].get('text')
                if path is None or text is None:
                    reasons.add('non_utf8_match'); continue
                if seen < q['offset']:
                    seen += 1; continue
                excerpt = text.rstrip('\r\n')[:500]
                item = {'path': names[path], 'line': data['line_number'], 'text': excerpt,
                        'excerpt_truncated': len(text.rstrip('\r\n')) > 500}
                cost = len(json.dumps(item, ensure_ascii=False).encode())
                if len(items) >= q['limit'] or size + cost > 60000:
                    reasons.add('result_limit'); return True
                items.append(item); seen += 1; size += cost
            code = process.wait(timeout=max(.1, deadline - time.monotonic()))
            if code not in (0, 1):
                raise ValueError('Search failed: invalid expression or unreadable input')
            return bool(reasons & {'time_limit', 'oversized_record'})
        finally:
            for fd in descriptors:
                os.close(fd)

    try:
        if not allowed(root):
            return {'error': 'Search path is outside the allowed file-tool scope'}
        batch = []
        for raw in candidates():
            batch.append(raw)
            if len(batch) >= 64:
                stop = scan(batch); batch = []
                if stop:
                    break
        else:
            if batch:
                scan(batch)
        return {'matches': items, 'count': len(items), 'offset': q['offset'],
                'next_offset': seen if 'result_limit' in reasons and seen < 10000 else None,
                'truncated': bool(reasons), 'reasons': sorted(reasons),
                'consistency': 'live; pagination rescans in path order, files may change'}
    except subprocess.TimeoutExpired:
        return {'matches': items, 'count': len(items), 'offset': q['offset'],
                'next_offset': None, 'truncated': True, 'reasons': ['time_limit'],
                'consistency': 'live; files may change'}
    except (ValueError, OSError):
        return {'error': 'Search failed: invalid expression, unreadable path or resource limit'}
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            process.stdout.close()


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False))
