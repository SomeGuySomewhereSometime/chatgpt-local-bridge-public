"""Check desktop MCP output and local enablement without accessing the desktop."""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('BRIDGE_WORKSPACE', '/home/bridge-user')
import mcp_server


class DesktopMCPTests(unittest.TestCase):
    def test_image_is_native_content_not_duplicate_base64_metadata(self):
        result = {'snapshot_id': 's', 'image': {'data': 'aGVsbG8=', 'width': 10, 'height': 10}}
        r = mcp_server.desktop_result(result)
        self.assertEqual([c.type for c in r.content], ['text', 'image'])
        self.assertEqual(r.content[1].data, 'aGVsbG8=')
        self.assertNotIn('aGVsbG8=', r.content[0].text)
        self.assertNotIn('data', r.structured_content['image'])

    def test_partial_failure_retains_receipts_and_native_image(self):
        r = mcp_server.desktop_result({'ok': False, 'completed': 1, 'observation': {
            'image': {'data': 'aGVsbG8=', 'width': 10}}})
        self.assertTrue(r.is_error)
        self.assertEqual(r.structured_content['completed'], 1)
        self.assertEqual(r.content[1].type, 'image')

    def test_disabled_desktop_never_starts_helper(self):
        with patch.object(mcp_server.bridge, 'BRIDGE_CONFIG', {}), patch.object(mcp_server.desktop_client, 'call') as call:
            with self.assertRaisesRegex(mcp_server.ToolError, 'disabled'):
                mcp_server.desktop_status()
            call.assert_not_called()

    def test_enabled_desktop_uses_only_fixed_operation(self):
        with patch.object(mcp_server.bridge, 'BRIDGE_CONFIG', {'desktop': {'enabled': True}}), patch.object(mcp_server.desktop_client, 'call', return_value={}) as call:
            mcp_server.desktop_status()
            call.assert_called_once_with('status')

    def test_worker_owns_projection_and_resolution(self):
        locator = {'role': 'button', 'name': 'Play', 'document': 'Video'}
        with patch.object(mcp_server, 'desktop_call', return_value={'snapshot_id': 's', 'elements': []}) as call:
            mcp_server.desktop_observe(application='Firefox', max_elements=12)
            self.assertEqual(call.call_args.kwargs['max_elements'], 12)
            self.assertEqual(call.call_args.kwargs['mode'], 'compact')
            mcp_server.desktop_query('s', locator, offset=20)
            self.assertEqual(call.call_args.args[0], 'query')
            mcp_server.desktop_act('s', [{'kind': 'activate', 'locator': locator}])
            self.assertEqual(call.call_args.kwargs['actions'][0]['locator'], locator)
        self.assertFalse(hasattr(mcp_server, '_DESKTOP_VIEW'))

    def test_exported_schema_documents_locator_and_modes(self):
        import asyncio
        async def check():
            tools = await __import__('test_profile_helpers').desktop_tools()
            by_name = {t.name: t for t in tools}
            observation = by_name['desktop_observe'].input_schema
            self.assertEqual(observation['properties']['mode']['enum'], ['compact', 'full'])
            action = by_name['desktop_act'].input_schema
            self.assertIn('DesktopLocator', str(action))
            self.assertIn('document', str(action))
            self.assertIn('desktop_query', by_name)
        asyncio.run(check())

    def test_browser_failure_preserves_receipt_as_mcp_error(self):
        import asyncio
        from unittest.mock import AsyncMock
        for status in ('target_missed','unconfirmed','target_hit'):
            outcome = dict(ok=status=='target_hit',click_status=status,
                           click_sent=True,hover_verified=True,verified=False)
            with patch.object(mcp_server.bridge,'BRIDGE_CONFIG',{'desktop':{'enabled':True}}), \
                 patch('browser_desktop.browser_pointer_action',new_callable=AsyncMock,return_value=outcome) as action:
                result = asyncio.run(mcp_server.desktop_browser_act(12,'button','sid',0,'Fixture',
                                     click_scope='reversible'))
                self.assertEqual(result.is_error,status!='target_hit')
                self.assertEqual({key: result.structured_content[key] for key in outcome}, outcome)
                self.assertFalse(result.structured_content['replayed'])
                self.assertIn('request_id', result.structured_content)
                self.assertTrue(result.structured_content['click_sent'])
                self.assertEqual(action.call_args.kwargs['click_scope'],'reversible')

    def test_browser_schema_requires_explicit_click_scope_and_warns_about_race(self):
        import asyncio
        async def check():
            tools = await __import__('test_profile_helpers').desktop_tools()
            tool = next(t for t in tools if t.name=='desktop_browser_act')
            field = tool.input_schema['properties']['click_scope']
            self.assertEqual(field['default'],'unspecified')
            self.assertEqual(field['enum'],['unspecified','reversible'])
            self.assertIn('not atomic',tool.description)
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
