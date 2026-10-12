"""Opt-in live Firefox test. Creates and closes its own localhost tab; never sends a chat."""
import argparse
import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from security import minimal_environment

TITLE = 'Bridge browser acceptance 067'
HTML = '''<html><head><title>Bridge browser acceptance 067</title></head><body>
<script>for(let i=0;i<450;i++){let d=document.createElement('div');d.textContent='Structure '+i;document.body.appendChild(d)}</script>
<input aria-label="Bridge test input" value="Olá mundo" style="position:fixed;top:30px;left:20px">
<button style="position:fixed;top:80px;left:20px" onclick="this.textContent='Verified: '+document.querySelector('input').value">Verify bridge</button>
</body></html>'''


async def main(exercise=False, text_probe=False):
    root=Path(__file__).resolve().parent
    env=minimal_environment(desktop=True);env['BRIDGE_WORKSPACE']='/home/bridge-user'
    params=StdioServerParameters(command=str(Path(__file__).resolve().parent / '.venv/bin/python'),
                                args=['-B',str(root/'mcp_server.py')],env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            async def call(name,args):
                r=await asyncio.wait_for(session.call_tool(name,args),25)
                if r.is_error:raise RuntimeError(str(r.content)[:1800])
                return r.structured_content or json.loads(r.content[0].text)
            async def observe():
                return await call('desktop_observe',{'application':'Firefox','max_elements':20,'scan_ms':10000})
            async def act(obs,action):
                r=await call('desktop_act',{'snapshot_id':obs['snapshot_id'],'actions':[action],'wait_ms':500})
                if not r['ok']:raise RuntimeError(json.dumps({k:r[k] for k in ('error','results')},ensure_ascii=False))
                return r
            obs=await observe()
            buttons=await call('desktop_query',{'snapshot_id':obs['snapshot_id'],'locator':{'name_contains':'novo separador'},'max_elements':5})
            print('FIREFOX_SCAN',json.dumps({'windows':[{'name':w['name'],'states':w['states']} for w in obs['windows']],
                'scan':obs.get('scan'),'compact':obs.get('compact'),'diagnostics':obs.get('diagnostics',''),
                'new_tab_controls':[{'name':e['name'],'role':e['role'],'states':e['states']} for e in buttons['elements']]}),flush=True)
            if not exercise:return
            assert obs['windows'], 'Firefox is not accessible'
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    body=HTML.encode()
                    self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8')
                    self.send_header('Content-Length',str(len(body)))
                    self.end_headers();self.wfile.write(body)
                def log_message(self,*args):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            # An independent fixture window avoids navigating away from an existing draft.
            # Opening it is part of --exercise, not an automatic action fallback in the Bridge.
            subprocess.run(['/usr/bin/firefox','--new-window',f'http://127.0.0.1:{server.server_port}/'],
                           env=env,timeout=15,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            await asyncio.sleep(2)
            try:
                obs=await call('desktop_observe',{'application':'Firefox','window':TITLE,'max_elements':20,'scan_ms':10000})
                print('FIXTURE_SCAN',json.dumps({'windows':obs['windows'],'scan':obs.get('scan'),'compact':obs.get('compact')}),flush=True)
                found=await call('desktop_query',{'snapshot_id':obs['snapshot_id'],'locator':{'document':TITLE,'name':'Verify bridge'}})
                assert len(found['elements'])==1, found.get('compact')
                print('FIREFOX_LATE_CONTROL_FOUND',json.dumps({'scan':obs['scan'],'target':found['elements'][0]['id']}),flush=True)
                r=await act(obs,{'kind':'focus','locator':{'document':TITLE,'name':'Bridge test input'},'expect':{'states':{'focused':True}}})
                assert r['results'][0]['verified']
                r=await act(r['observation'],{'kind':'activate','locator':{'document':TITLE,'name':'Verify bridge'},'expect':{'text':'Verified: Olá mundo'}})
                assert r['results'][0]['verified']
                print('FIREFOX_FOCUS_AND_PAGE_EFFECT_VERIFIED (text was prefilled by fixture)',flush=True)
                if text_probe:
                    r=await act(r['observation'],{'kind':'set_text','locator':{'document':TITLE,'name':'Bridge test input'},'text':'Novo texto'})
                    assert r['results'][0]['verified']
                    print('FIREFOX_TEXT_WRITE_VERIFIED',flush=True)
            finally:
                # Only close a tab that positively identifies our fixture.
                try:
                    obs=await call('desktop_observe',{'application':'Firefox','window':TITLE,'max_elements':20,'scan_ms':10000})
                    q=await call('desktop_query',{'snapshot_id':obs['snapshot_id'],'locator':{'role':'page tab','name':TITLE}})
                    if len(q['elements'])==1:
                        await act(obs,{'kind':'activate','locator':{'name':'Fechar separador','ancestor_role':'page tab','ancestor_name':TITLE}})
                        print('FIREFOX_FIXTURE_CLOSED',flush=True)
                finally:
                    server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise',action='store_true')
    parser.add_argument('--text-probe',action='store_true',help='Also require AT-SPI text replacement (known Firefox limitation)')
    args=parser.parse_args()
    asyncio.run(main(args.exercise or args.text_probe,args.text_probe))
