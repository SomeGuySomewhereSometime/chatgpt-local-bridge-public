"""Regression tests for MCP v1/v2 readiness probes; no browser actions."""
import importlib.util
import json
from pathlib import Path
import tempfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mcp
import mcp.client

spec = importlib.util.spec_from_file_location('bridge_service_probes', Path(__file__).parent/'ops/mcp-services.py')
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


class ServiceTransportTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, *, modern=True, count=2):
        calls = []
        @asynccontextmanager
        async def factory(url, **kwargs):
            calls.append((url, kwargs))
            yield ('read', 'write', 'session-id')[:count]
        class HttpClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                self.closed = True
        module = SimpleNamespace()
        if modern:
            module.httpx2 = SimpleNamespace(AsyncClient=HttpClient, Timeout=lambda value: ('timeout', value))
            module.streamable_http_client = factory
        else:
            module.streamablehttp_client = factory
        return module, calls

    async def test_modern_transport_uses_client_timeout_and_two_streams(self):
        module, calls = self.transport()
        with patch.object(mcp.client, 'streamable_http', module):
            async with services.http_streams('http://localhost/mcp', 4) as streams:
                self.assertEqual(streams, ('read', 'write'))
        client = calls[0][1]['http_client']
        self.assertEqual(client.kwargs, {'timeout': ('timeout', 4), 'trust_env': False})
        self.assertTrue(client.closed)
        self.assertNotIn('timeout', calls[0][1])

    async def test_modern_alias_in_v1_can_still_return_three_streams(self):
        module, _ = self.transport(count=3)
        module.httpx = module.httpx2
        del module.httpx2
        with patch.object(mcp.client, 'streamable_http', module):
            async with services.http_streams('http://localhost/mcp', 5) as streams:
                self.assertEqual(streams, ('read', 'write'))

    async def test_legacy_transport_preserves_its_timeout_argument(self):
        module, calls = self.transport(modern=False, count=3)
        with patch.object(mcp.client, 'streamable_http', module):
            async with services.http_streams('http://localhost/mcp', 5) as streams:
                self.assertEqual(streams, ('read', 'write'))
        self.assertEqual(calls[0][1], {'timeout': 5})

    def session(self, *, modern=True, names=None, error=False):
        calls = []
        class Session:
            def __init__(self, read, write):
                self.inputs = (read, write)
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            async def initialize(self):
                info = SimpleNamespace(version='test-version')
                return SimpleNamespace(**({'server_info': info} if modern else {'serverInfo': info}))
            async def list_tools(self):
                return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in names or []])
            async def call_tool(self, name, arguments):
                calls.append(name)
                result = {'state': 'test'}
                fields = {'is_error': error, 'structured_content': result} if modern else {'isError': error, 'structuredContent': result}
                return SimpleNamespace(**fields)
        return Session, calls

    async def test_browser_probe_lists_catalogue_without_browser_actions(self):
        module, _ = self.transport()
        session, calls = self.session(names=['browser_tabs','browser_fill_form','browser_snapshot'])
        with patch.object(mcp.client,'streamable_http',module), patch.object(mcp,'ClientSession',session):
            result = await services.browser_probe()
        self.assertTrue(result['mcp'])
        self.assertEqual(calls, [])
        self.assertIsNone(result['chrome_extension']['connected'])

    async def test_incomplete_browser_catalogue_is_not_ready(self):
        module, _ = self.transport()
        session, _ = self.session(names=['browser_tabs'])
        with patch.object(mcp.client,'streamable_http',module), patch.object(mcp,'ClientSession',session):
            self.assertFalse((await services.browser_probe())['mcp'])

    async def check_unity(self, *, modern=True, error=False):
        module, _ = self.transport(modern=modern, count=2 if modern else 3)
        session, calls = self.session(modern=modern, names=['editor-application-get-state','scene-list-opened'],error=error)
        with tempfile.TemporaryDirectory() as temporary:
            project=Path(temporary)
            (project/'UserSettings').mkdir()
            (project/'UserSettings/AI-Game-Developer-Config.json').write_text(json.dumps({'host':'http://localhost:1234'}))
            with patch.object(services,'PROJECT',project), patch.object(mcp.client,'streamable_http',module), patch.object(mcp,'ClientSession',session):
                result = await services.unity_probe()
        self.assertEqual(calls,['editor-application-get-state','scene-list-opened'])
        return result

    async def test_unity_v2_fields_report_live_state(self):
        result = await self.check_unity()
        self.assertTrue(result['application'])
        self.assertEqual(result['server_version'],'test-version')
        self.assertEqual(result['scenes'],{'state':'test'})

    async def test_unity_v1_fields_remain_supported(self):
        result = await self.check_unity(modern=False)
        self.assertTrue(result['application'])
        self.assertEqual(result['editor_state'],{'state':'test'})

    async def test_unity_tool_error_does_not_claim_application_ready(self):
        self.assertFalse((await self.check_unity(error=True))['application'])


if __name__ == '__main__':
    unittest.main()
