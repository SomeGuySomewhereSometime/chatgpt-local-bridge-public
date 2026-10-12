import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BRIDGE_WORKSPACE", "/home/bridge-user")

import bridge
from fastapi import HTTPException


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "project").mkdir()
        for name, value in [("WORKSPACE", self.root), ("COMMAND_WORKSPACE", self.root), ("BRIDGE_CONFIG", {})]:
            mocked = patch.object(bridge, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_system_info_and_process_list(self):
        info = bridge.system_info()
        self.assertEqual(info["workspace"], str(self.root))
        self.assertEqual(
            info["command_workspace"],
            str(self.root),
        )
        self.assertIn("kernel", info)
        result = bridge.process_list(bridge.ProcessListRequest(limit=5))
        self.assertLessEqual(result["count"], 5)
        self.assertIn("processes", result)
        for item in result["processes"]:
            self.assertNotIn("args", item)

    def test_run_command_workspace_matches_general_workspace(self):
        self.assertEqual(
            bridge.COMMAND_WORKSPACE,
            self.root,
        )
        self.assertEqual(
            bridge.safe_command_path("."),
            self.root,
        )
        with self.assertRaises(HTTPException) as ctx:
            bridge.safe_command_path("../../etc")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_process_list_can_include_args_explicitly(self):
        result = bridge.process_list(
            bridge.ProcessListRequest(query="python", limit=20, include_args=True)
        )
        self.assertTrue(result["include_args"])
        for item in result["processes"]:
            self.assertIn("args", item)

    def test_process_info_reads_current_user_process_without_environ(self):
        result = bridge.process_info(bridge.ProcessInfoRequest(pid=os.getpid()))
        self.assertEqual(result["pid"], os.getpid())
        self.assertEqual(result["owner_uid"], os.getuid())
        self.assertTrue(result["executable"])
        self.assertIn("pid", result["namespaces"])
        self.assertNotIn("environ", result)

    def test_journal_query_is_bounded_and_filters_text(self):
        fake = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="AppArmor denied demo\n", stderr=""
        )
        with patch.object(bridge.subprocess, "run", return_value=fake) as run:
            result = bridge.journal_query(
                bridge.JournalQueryRequest(query="apparmor", since_minutes=5, limit=10, kernel_only=True)
            )
        self.assertEqual(result["lines"], ["AppArmor denied demo"])
        self.assertIn("--dmesg", run.call_args.args[0])
        self.assertIn('--grep', run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index('-n')+1], '10')

    def test_journal_no_matches_is_an_empty_result_not_an_error(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='')
        with patch.object(bridge.subprocess, 'run', return_value=fake):
            result = bridge.journal_query(bridge.JournalQueryRequest(query='no-such-entry'))
        self.assertEqual(result['count'], 0)
        self.assertEqual(result['lines'], [])

    def test_process_kill_owned_allowlisted_child(self):
        child = subprocess.Popen(["/usr/bin/sleep", "30"])
        policy = {
            "process_kill": {
                "allowed_executables": ["/usr/bin/sleep"],
                "allowed_signals": ["TERM"],
            }
        }
        try:
            with patch.object(bridge, "BRIDGE_CONFIG", policy):
                result = bridge.process_kill(
                    bridge.ProcessKillRequest(pid=child.pid, signal="TERM")
                )
            self.assertTrue(result["ok"])
            child.wait(timeout=3)
        finally:
            if child.poll() is None:
                child.kill()

    def test_process_kill_denies_unallowlisted_process(self):
        child = subprocess.Popen(["/usr/bin/sleep", "30"])
        try:
            with patch.object(bridge, "BRIDGE_CONFIG", {}):
                with self.assertRaises(HTTPException) as ctx:
                    bridge.process_kill(bridge.ProcessKillRequest(pid=child.pid))
            self.assertEqual(ctx.exception.status_code, 403)
        finally:
            child.kill()
            child.wait(timeout=3)

    def test_process_kill_protects_pid_one(self):
        with self.assertRaises(HTTPException) as ctx:
            bridge.process_kill(bridge.ProcessKillRequest(pid=1))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mkdir_move_delete(self):
        root = ".chatgpt-bridge/unittest"
        bridge.mkdir(bridge.MkdirRequest(path=f"{root}/a"))
        file_path = bridge.safe_path(f"{root}/a/x.txt")
        file_path.write_text("ok", encoding="utf-8")
        moved = bridge.move(
            bridge.MoveRequest(
                source=f"{root}/a/x.txt",
                destination=f"{root}/a/y.txt",
            )
        )
        self.assertTrue(moved["ok"])
        bridge.delete(bridge.DeleteRequest(path=f"{root}/a/y.txt"))
        bridge.delete(bridge.DeleteRequest(path=root, recursive=True))
        self.assertFalse(bridge.safe_path(root).exists())

    @unittest.skipUnless(os.environ.get("BRIDGE_SYSTEMD_TESTS") == "1", "requires user systemd")
    def test_app_launch_and_close_use_allowlist_and_handle(self):
        policy = {
            "applications": {
                "sleeper": {
                    "executable": "/usr/bin/sleep",
                    "allow_args": True,
                }
            }
        }
        with patch.object(bridge, "BRIDGE_CONFIG", policy):
            launched = bridge.app_launch(
                bridge.AppLaunchRequest(
                    app="sleeper",
                    args=["30"],
                    cwd="project",
                )
            )
            self.assertIn("handle", launched)
            closed = bridge.app_close(
                bridge.AppCloseRequest(handle=launched["handle"])
            )
        self.assertTrue(closed["ok"])
        bridge.LAUNCHED_APPS[launched["handle"]].wait(timeout=3)

    def test_app_launch_denies_unallowlisted_application(self):
        with patch.object(bridge, "BRIDGE_CONFIG", {}):
            with self.assertRaises(HTTPException) as ctx:
                bridge.app_launch(bridge.AppLaunchRequest(app="python3"))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_app_launch_respects_argument_policy(self):
        policy = {
            "applications": {
                "sleeper": {
                    "executable": "/usr/bin/sleep",
                    "allow_args": False,
                }
            }
        }
        with patch.object(bridge, "BRIDGE_CONFIG", policy):
            with self.assertRaises(HTTPException) as ctx:
                bridge.app_launch(
                    bridge.AppLaunchRequest(app="sleeper", args=["30"])
                )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_app_close_rejects_unknown_handle(self):
        with self.assertRaises(HTTPException) as ctx:
            bridge.app_close(bridge.AppCloseRequest(handle="not-a-real-handle"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_screen_capture_portal_is_primary_backend(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake-png")
            source = Path(tmp.name)
        target = "chatgpt-local-bridge/.screen-unit-test.png"
        try:
            with patch.object(bridge, "_portal_capture", return_value=source.as_uri()), \
                 patch.object(bridge, "_gnome_capture") as gnome:
                result = bridge.screen_capture(bridge.ScreenCaptureRequest(path=target))
            self.assertEqual(result["backend"], "xdg-desktop-portal")
            gnome.assert_not_called()
            self.assertEqual(bridge.safe_path(target).read_bytes(), b"fake-png")
        finally:
            source.unlink(missing_ok=True)
            bridge.safe_path(target).unlink(missing_ok=True)

    def test_screen_capture_gnome_fallback(self):
        target = "chatgpt-local-bridge/.screen-unit-test.png"

        def fake_gnome(destination: Path, include_cursor: bool):
            destination.write_bytes(b"fake-png")

        try:
            with patch.object(bridge, "_portal_capture", side_effect=RuntimeError("denied")), \
                 patch.object(bridge, "_gnome_capture", side_effect=fake_gnome):
                result = bridge.screen_capture(bridge.ScreenCaptureRequest(path=target))
            self.assertEqual(result["backend"], "org.gnome.Shell.Screenshot")
        finally:
            bridge.safe_path(target).unlink(missing_ok=True)

    def test_screen_capture_portal_result_is_copied_into_workspace(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake-png")
            source = Path(tmp.name)
        target = "chatgpt-local-bridge/.screen-unit-test.png"
        try:
            with patch.object(bridge, "_portal_capture", return_value=source.as_uri()):
                result = bridge.screen_capture(
                    bridge.ScreenCaptureRequest(path=target)
                )
            self.assertEqual(result["backend"], "xdg-desktop-portal")
            self.assertEqual(bridge.safe_path(target).read_bytes(), b"fake-png")
        finally:
            source.unlink(missing_ok=True)
            bridge.safe_path(target).unlink(missing_ok=True)

    def test_screen_capture_rejects_non_png_destination(self):
        with self.assertRaises(HTTPException) as ctx:
            bridge.screen_capture(bridge.ScreenCaptureRequest(path="bad.jpg"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_delete_refuses_workspace_root(self):
        with self.assertRaises(HTTPException) as ctx:
            bridge.delete(bridge.DeleteRequest(path=".", recursive=True))
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
