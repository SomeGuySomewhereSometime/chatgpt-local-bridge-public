"""Fresh QuickJS interpreter. Only JSON IPC callbacks, no host modules in JS."""
import json
import resource
import sys


def main():
    import quickjs
    resource.setrlimit(resource.RLIMIT_CPU, (3, 4))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    request = json.loads(sys.stdin.readline(150000))
    ctx = quickjs.Context()
    ctx.set_memory_limit(32 * 1024 * 1024)
    ctx.set_max_stack_size(512 * 1024)

    def rpc(kind, payload):
        print(json.dumps({'kind': kind, 'payload': json.loads(payload)}), flush=True)
        reply = sys.stdin.readline(4 * 1024 * 1024)
        if not reply:
            raise RuntimeError('Controller disconnected')
        return reply

    ctx.add_callable('__rpc', rpc)
    catalog = json.dumps(request['tools'])
    ctx.eval('''
        const bridgeRPC = __rpc;
        delete globalThis.__rpc;
        const ALL_TOOLS = Object.freeze(''' + catalog + ''');
        function dispatch(kind, value) {
            const reply = JSON.parse(bridgeRPC(kind, JSON.stringify(value)));
            if (!reply.ok) throw new Error(reply.error);
            return reply.result;
        }
        const tools = Object.freeze(Object.fromEntries(ALL_TOOLS.map(t =>
            [t.name, (args = {}) => dispatch('tool', {name:t.name, arguments:args})])));
        const text = value => dispatch('text', value === undefined ? null : value);
        globalThis.__finished = false;
        globalThis.__failure = null;
    ''')
    try:
        ctx.eval('(async () => {\n' + request['code'] + '\n})().then(() => {__finished=true}, e => {__failure=String(e);__finished=true});')
        while ctx.execute_pending_job():
            pass
        if not ctx.eval('__finished'):
            raise RuntimeError('Unresolved promise; no background JS survives this call')
        error = ctx.eval('__failure')
        print(json.dumps({'kind': 'done', 'error': error}), flush=True)
    except Exception as exc:
        print(json.dumps({'kind': 'done', 'error': str(exc)[:2000]}), flush=True)


if __name__ == '__main__':
    main()
