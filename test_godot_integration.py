"""Readiness and conservative recovery contracts; no live editor mutations."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "ops" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

entry = load("godot_entry")
control = load("godot_control")

class Readiness(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_is_not_ready(self):
        bridge = SimpleNamespace(connected=False, send=AsyncMock())
        self.assertFalse((await entry.readiness(bridge))["ready"])
        bridge.send.assert_not_called()

    async def test_connected_without_real_response_is_not_ready(self):
        bridge = SimpleNamespace(connected=True, send=AsyncMock(side_effect=TimeoutError))
        self.assertFalse((await entry.readiness(bridge))["ready"])

    async def test_wrong_project_does_not_report_ready(self):
        bridge = SimpleNamespace(connected=True, send=AsyncMock(return_value=SimpleNamespace(ok=True, result={"project_path":"/other"})))
        state = await entry.readiness(bridge)
        self.assertTrue(state["application"])
        self.assertFalse(state["ready"])

    async def test_real_expected_project_is_ready(self):
        bridge = SimpleNamespace(connected=True, send=AsyncMock(return_value=SimpleNamespace(ok=True, result={"project_path":entry.PROJECT + "/", "name":"Warriors"})))
        self.assertTrue((await entry.readiness(bridge))["ready"])
        self.assertEqual(bridge.send.call_args.args, ("cmd_get_project_info",))

class Recovery(unittest.TestCase):
    def test_healthy_editor_is_never_restarted(self):
        with patch.object(control, "fetch", side_effect=[{"live":True}, {"ready":True}]), patch.object(control,"editor_pids",return_value=[123]), patch.object(control,"systemctl") as ctl, patch.object(control,"PROFILE",Mock(is_file=Mock(return_value=False))):
            result=control.recover(True)
            ctl.assert_not_called()
            self.assertEqual(result["editor_pids"], [123])

    def test_disconnected_editor_is_preserved(self):
        with patch.object(control,"fetch",side_effect=lambda route: {"live":True} if route == "live" else {"ready":False}), patch.object(control,"editor_pids",return_value=[123]), patch.object(control,"systemctl") as ctl, patch.object(control.time,"sleep"), patch.object(control,"PROFILE",Mock(is_file=Mock(return_value=False))):
            self.assertFalse(control.recover(True)["ready"])
            ctl.assert_not_called()

    def test_closed_editor_only_opens_when_requested(self):
        for open_app in (False, True):
            with self.subTest(open_app=open_app), patch.object(control,"fetch",side_effect=lambda route: {"live":True} if route == "live" else {"ready":True}), patch.object(control,"editor_pids",return_value=[]), patch.object(control,"systemctl") as ctl, patch.object(control,"PROFILE",Mock(is_file=Mock(return_value=False))):
                control.recover(open_app)
                self.assertEqual(ctl.call_count, int(open_app))
                if open_app:
                    ctl.assert_called_once_with("start", control.APP_UNIT)
