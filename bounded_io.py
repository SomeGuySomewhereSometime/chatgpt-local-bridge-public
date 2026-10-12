"""Bounded UTF-8 pages and a validated host ripgrep collector."""
import hashlib
import os
from pathlib import Path
import stat
import time


def version(info):
    return hashlib.sha256(str((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)).encode()).hexdigest()[:32]


def read_range(path, path_check, *, start_line=1, line_count=200, byte_offset=0, expected_version='', full=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        path_check(str(Path(f'/proc/self/fd/{fd}').resolve()))
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('File must be regular UTF-8 text')
        token = version(before)
        if expected_version and token != expected_version:
            raise ValueError('File changed; start a new read')
        if full and (byte_offset or start_line != 1):
            raise ValueError('full cannot be combined with a range or byte offset')
        if full and before.st_size > 2_000_000:
            raise ValueError('Full reads are limited to 2 MB; use a page')
        if byte_offset > before.st_size:
            raise ValueError('byte_offset exceeds file size')
        stream.seek(byte_offset)
        if not byte_offset:
            skipped = 0
            deadline = time.monotonic() + 1
            for _ in range(start_line - 1):
                while True:
                    chunk = stream.readline(65536)
                    skipped += len(chunk)
                    if skipped > 8_000_000 or time.monotonic() > deadline:
                        raise ValueError('Line seek budget exceeded; continue using byte offsets')
                    if not chunk or chunk.endswith(b'\n'):
                        break
                if not chunk:
                    break
        start = stream.tell()
        raw = b''
        if full:
            raw = stream.read(2_000_001)
        else:
            for _ in range(line_count):
                chunk = stream.readline(65536 - len(raw))
                raw += chunk
                if not chunk or len(raw) == 65536:
                    break
        # Do not split a multibyte character at the byte budget boundary.
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            if not full and exc.reason == 'unexpected end of data' and exc.end == len(raw):
                raw = raw[:exc.start]
                text = raw.decode('utf-8')
            else:
                raise ValueError('File page is not UTF-8 text') from None
        after = os.fstat(fd)
        if version(after) != token or version(os.stat(path)) != token:
            raise ValueError('File changed during read; start a new read')
        end = start + len(raw)
        more = end < before.st_size
        newlines = raw.count(b'\n')
        return {'content': text, 'version': token, 'start_line': start_line,
                'end_line': start_line + newlines - (1 if raw.endswith(b'\n') else 0) if raw else None,
                'byte_offset': start, 'bytes_read': len(raw), 'has_more': more,
                'partial_line': bool(more and raw and not raw.endswith(b'\n')),
                'next_byte_offset': end if more else None,
                'next_line': start_line + newlines if more else None}


SEARCH_WORKER = Path(__file__).with_name('search_worker.py').read_text(encoding='utf-8')
