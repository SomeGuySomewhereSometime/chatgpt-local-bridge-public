"""Browser/portal orchestration with deterministic transports, no live input."""
import copy
import json
import threading
import unittest
from types import SimpleNamespace
from browser_desktop import browser_pointer_action

class FakeBrowser:
    def __init__(self, client, fail_read=0, readback_error=False):
        self.client = client
        self.fail_read = fail_read
        self.reads = 0
        self.readback_error = readback_error
        self.readback = dict(click=dict(target=True, client=[300,240]))
        self.prepare_error = False

    async def call_tool(self, name, args):
        function = args.get('function', '')
        value = dict(title='Fixture',bounds=[200,200,200,80],viewport=[1000,800,1])
        error = False
        if name == 'browser_tabs': value = []
        elif function == '() => document.title': value = 'Fixture'
        elif '.read()' in function:
            self.reads += 1
            if self.reads == self.fail_read:
                value = 'Browser target, document or viewport changed; recalibrate'
                error = True
            else:
                value['move'] = dict(client=self.client.position, target=True)
        elif '.prepareClick()' in function and self.prepare_error:
            value = 'Browser target, document or viewport changed; recalibrate'
            error = True
        elif '.result()' in function:
            if self.readback_error: raise RuntimeError('response lost after click')
            value = self.readback
        return SimpleNamespace(is_error=error,content=[SimpleNamespace(type='text',text=
            '### Result\n'+json.dumps(value)+'\n### End')])

class FakeClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.position = None
        self.clicks = 0
        self.finished = False
        self.delivery_error = False

    def call(self, operation, **args):
        command = args.get('command')
        if command == 'prepare':
            return dict(calibration_id='cal',window=dict(frame_bounds=[0,0,1000,800]),
                        image=dict(logical_position=[0,0],logical_size=[1000,800]))
        if command == 'move': self.position = copy.copy(args['position'])
        if command == 'click':
            self.clicks += 1
            if self.delivery_error: raise RuntimeError('lost receipt')
            return dict(executed=True,verified=False)
        if command == 'finish': self.finished = True
        return {}

class BrowserActionTests(unittest.IsolatedAsyncioTestCase):
    async def run_action(self, client, browser):
        return await browser_pointer_action(client,12,'button','sid',0,'Fixture',browser_session=browser,click_scope='reversible')

    async def test_valid_target_sends_exactly_one_click(self):
        client = FakeClient()
        result = await self.run_action(client,FakeBrowser(client))
        self.assertTrue(result['pointer_verified'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['click_status'],'target_hit')
        self.assertTrue(result['click_target_verified'])
        self.assertFalse(result['verified'])
        self.assertEqual(client.clicks,1)
        self.assertTrue(client.finished)

    async def test_stale_during_each_probe_or_final_hover_sends_zero_clicks(self):
        for read in range(1,6):
            with self.subTest(read=read):
                client = FakeClient()
                with self.assertRaisesRegex(ValueError,'recalibrate'):
                    await self.run_action(client,FakeBrowser(client,fail_read=read))
                self.assertEqual(client.clicks,0)
                self.assertTrue(client.finished)

    async def test_readback_failure_does_not_repeat_click(self):
        client = FakeClient()
        result = await self.run_action(client,FakeBrowser(client,readback_error=True))
        self.assertFalse(result['readback']['available'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['click_status'],'unconfirmed')
        self.assertTrue(result['click_sent'])
        self.assertEqual(client.clicks,1)

    async def test_dispatch_failure_does_not_repeat_click(self):
        client = FakeClient()
        client.delivery_error = True
        result = await self.run_action(client,FakeBrowser(client,readback_error=True))
        self.assertFalse(result['ok'])
        self.assertIsNone(result['click_sent'])
        self.assertIsNone(result['executed'])
        self.assertEqual(result['dispatch_status'],'unknown')
        self.assertEqual(client.clicks,1)
        self.assertTrue(client.finished)

    async def test_final_gap_miss_is_failure_with_receipt_and_no_retry(self):
        client = FakeClient()
        browser = FakeBrowser(client)
        browser.readback = dict(click=dict(target=False,client=[300,240]))
        result = await self.run_action(client,browser)
        self.assertFalse(result['ok'])
        self.assertTrue(result['hover_verified'])
        self.assertTrue(result['click_sent'])
        self.assertEqual(result['click_status'],'target_missed')
        self.assertFalse(result['click_target_verified'])
        self.assertFalse(result['automatic_retry'])
        self.assertEqual(client.clicks,1)

    async def test_missing_or_malformed_readback_is_uncertain(self):
        for value in [None, [], {}, {'click':None}, {'click':{}},
                      {'click':{'target':'true'}}, {'click':{'target':True}},
                      {'click':{'target':True,'client':[999,999]}}]:
            with self.subTest(value=value):
                client = FakeClient()
                browser = FakeBrowser(client)
                browser.readback = value
                result = await self.run_action(client,browser)
                self.assertFalse(result['ok'])
                self.assertEqual(result['click_status'],'unconfirmed')
                self.assertIsNone(result['click_target_verified'])
                self.assertEqual(client.clicks,1)

    async def test_final_geometry_recheck_refuses_before_dispatch(self):
        client = FakeClient()
        browser = FakeBrowser(client)
        browser.prepare_error = True
        with self.assertRaisesRegex(ValueError,'recalibrate'):
            await self.run_action(client,browser)
        self.assertEqual(client.clicks,0)
        self.assertTrue(client.finished)

    async def test_scope_gate_runs_before_any_transport(self):
        from unittest.mock import Mock
        for scope in ['unspecified','consequential',None]:
            client = Mock()
            with self.assertRaises(ValueError):
                await browser_pointer_action(client,12,'button','sid',0,'Fixture',click_scope=scope)
            client.call.assert_not_called()

    async def test_move_does_not_require_click_declaration(self):
        client = FakeClient()
        result = await browser_pointer_action(client,12,'button','sid',0,'Fixture',
            action='move',browser_session=FakeBrowser(client))
        self.assertTrue(result['ok'])
        self.assertEqual(result['click_status'],'not_requested')
        self.assertFalse(result['click_sent'])
        self.assertEqual(client.clicks,0)
