"""In-memory backend verifies persistent browser state across MCP requests."""
import asyncio
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from mcp import types
from starlette.testclient import TestClient

spec=importlib.util.spec_from_file_location('browser_relay',Path(__file__).parent/'ops/browser_proxy.py')
relay=importlib.util.module_from_spec(spec);spec.loader.exec_module(relay)


class BrowserProxyTests(unittest.TestCase):
    def setUp(self):
        outer=self
        self.calls=[]
        self.selected='initial'
        class Session:
            async def call_tool(inner,name,arguments):
                outer.calls.append((name,arguments))
                if arguments.get('select'):
                    outer.selected=arguments['select']
                return types.CallToolResult(content=[types.TextContent(type='text',text=outer.selected)],structured_content={'selected':outer.selected})
        self.state=relay.BackendState()
        self.state.session=Session()
        self.state.lock=asyncio.Lock()
        self.state.catalogue=types.ListToolsResult(tools=[types.Tool(name='browser_test',description='Test',input_schema={'type':'object','properties':{'select':{'type':'string'}}})])
        self.app=relay.create_app(self.state)
        self.client=TestClient(self.app,base_url='http://127.0.0.1:8931')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)

    def request(self,method,params=None):
        body={'jsonrpc':'2.0','id':1,'method':method}
        if params is not None:body['params']=params
        return self.client.post('/mcp',json=body,headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-03-26'})

    def test_oauth_absent_is_404_without_backend_calls(self):
        for path in ['/.well-known/oauth-protected-resource','/.well-known/oauth-protected-resource/mcp','/.well-known/oauth-authorization-server']:
            self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.calls,[])

    def test_nonlocal_host_is_rejected(self):
        self.assertIn(self.client.get('/mcp',headers={'Host':'example.invalid:8931'}).status_code,{400,403})
        self.assertEqual(self.calls,[])

    def test_unknown_path_is_404(self):
        self.assertEqual(self.client.get('/private-file').status_code,404)

    def test_tools_are_advertised_with_original_schema(self):
        response=self.request('tools/list')
        self.assertEqual(response.status_code,200)
        tools=response.json()['result']['tools']
        self.assertEqual(tools[0]['name'],'browser_test')
        self.assertEqual(tools[0]['inputSchema']['properties']['select'],{'type':'string'})

    def test_selected_page_survives_distinct_frontend_requests(self):
        first=self.request('tools/call',{'name':'browser_test','arguments':{'select':'test-page'}})
        self.assertEqual(first.status_code,200)
        second=self.request('tools/call',{'name':'browser_test','arguments':{}})
        self.assertEqual(second.json()['result']['structuredContent'],{'selected':'test-page'})
        self.assertEqual(len(self.calls),2)

    def test_unknown_tool_is_not_forwarded(self):
        response=self.request('tools/call',{'name':'unknown','arguments':{}})
        self.assertTrue(response.json()['result']['isError'])
        self.assertEqual(self.calls,[])

    def test_node_does_not_inherit_transport_credentials(self):
        with patch.dict(os.environ,{'PLAYWRIGHT_MCP_EXTENSION_TOKEN':'test-token','CONTROL_PLANE_API_KEY':'secret'},clear=True):
            self.assertEqual(relay.node_environment(),{'PLAYWRIGHT_MCP_EXTENSION_TOKEN':'test-token'})


if __name__=='__main__':unittest.main()
