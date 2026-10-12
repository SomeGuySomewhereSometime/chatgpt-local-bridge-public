import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

import bridge
import security


class ConfigurationTests(unittest.TestCase):
    def test_missing_or_incomplete_policy_refuses_startup(self):
        with tempfile.TemporaryDirectory(prefix="bridge-policy-") as tmp:
            path = Path(tmp) / "policy.json"
            with patch.object(bridge, "CONFIG_PATH", path):
                with self.assertRaisesRegex(RuntimeError, "Required bridge config"):
                    bridge.load_bridge_config()
                invalid = ["invalid JSON", "[]", "{}", '{"filesystem": null}',
                           '{"filesystem": {}}',
                           '{"filesystem":{"denied_paths":[],"read_only_paths":[]}}',
                           '{"filesystem":{"denied_paths":["relative"],"read_only_paths":["/control"]}}']
                for content in invalid:
                    with self.subTest(content=content):
                        path.write_text(content)
                        with self.assertRaises(RuntimeError):
                            bridge.load_bridge_config()
                valid = {"filesystem": {"denied_paths": ["/private"], "read_only_paths": ["/control"]}}
                path.write_text(json.dumps(valid))
                self.assertEqual(bridge.load_bridge_config(), valid)


@unittest.skipUnless(os.environ.get("BRIDGE_HOST_TESTS") == "1", "requires the host user systemd manager")
class GitIdentityTests(unittest.TestCase):
    def test_host_git_uses_normal_global_config_and_local_identity_wins(self):
        with tempfile.TemporaryDirectory(prefix="bridge-git-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (home / ".gitconfig").write_text(
                '[user]\nname = Global Example\nemail = global@example.invalid\n'
                '[credential]\nhelper = forbidden-helper\n'
                '[core]\nhooksPath = /forbidden-hooks\n'
                '[commit]\ngpgSign = true\n')
            env = security.minimal_environment()
            env["HOME"] = str(home)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
            with patch.object(security, "minimal_environment", return_value=env):
                run = lambda args: security.run_host(repo, repo, {}, args)
                initial = run(["git", "var", "GIT_AUTHOR_IDENT"])
                self.assertEqual(initial.returncode, 0, initial.stderr)
                self.assertIn("Global Example <global@example.invalid>", initial.stdout)
                for key in ["credential.helper", "core.hooksPath", "commit.gpgSign"]:
                    result = run(["git", "config", "--get", key])
                    self.assertEqual(result.returncode, 0, result.stderr)
                diagnostic = security.run_host(repo, repo, {}, ['git', 'config', '--get', 'credential.helper'], read_only=True)
                self.assertEqual(diagnostic.returncode, 1, diagnostic.stdout)
                for key, value in [("user.name", "Project Example"), ("user.email", "project@example.invalid")]:
                    subprocess.run(["git", "-C", str(repo), "config", key, value], check=True, env=env)
                specific = run(["git", "var", "GIT_AUTHOR_IDENT"])
                self.assertIn("Project Example <project@example.invalid>", specific.stdout)


@unittest.skipUnless(os.environ.get("BRIDGE_SYSTEMD_TESTS") == "1", "requires user systemd")
class ApplicationLifecycleTests(unittest.TestCase):
    def test_application_survives_stopping_its_launcher_service(self):
        source = str(Path(__file__).resolve().parent)
        parent_unit = "bridge-regression-" + uuid.uuid4().hex + ".service"
        env = bridge.desktop_environment()
        app_unit = None
        # Run the real Bridge launcher inside a disposable service with the same
        # KillMode as the tunnel. No running Editor participates in this test.
        child_code = '''import json,os,sys,time
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"pid":os.getpid(),"cgroup":Path("/proc/self/cgroup").read_text(),"inherited_key":"CONTROL_PLANE_API_KEY" in os.environ,"literal":sys.argv[2]}))
time.sleep(60)
'''
        parent_code = '''import json,sys,time
from pathlib import Path
import bridge
root=Path(sys.argv[1])
bridge.WORKSPACE=root
bridge.BRIDGE_CONFIG={"applications":{"probe":{"executable":"/usr/bin/python3","fixed_args":["-B","-c",sys.argv[2],str(root/"child.json"),"literal$HOME%h"],"allow_args":False}}}
result=bridge.app_launch(bridge.AppLaunchRequest(app="probe"))
(root/"launch.json").write_text(json.dumps(result))
time.sleep(60)
'''
        with tempfile.TemporaryDirectory(prefix="bridge-lifecycle-") as tmp:
            root = Path(tmp)
            command = ["systemd-run", "--user", "--quiet", "--collect", "--unit=" + parent_unit,
                       "--service-type=exec", "--property=KillMode=control-group", "--expand-environment=no",
                       "--", "/usr/bin/env", "-i", *[f"{k}={v}" for k,v in env.items()],
                       "PYTHONPATH=" + source, "CONTROL_PLANE_API_KEY=synthetic-not-a-credential",
                       sys.executable, "-B", "-c", parent_code, str(root), child_code]
            try:
                subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=15)
                deadline = time.monotonic() + 12
                while not ((root/"launch.json").exists() and (root/"child.json").exists()):
                    if time.monotonic() > deadline:
                        self.fail("Disposable launcher did not become ready")
                    time.sleep(0.1)
                launched = json.loads((root/"launch.json").read_text())
                child = json.loads((root/"child.json").read_text())
                app_unit = launched["unit"]
                self.assertTrue(app_unit.startswith("mcp-app-"))
                self.assertIn(app_unit, child["cgroup"])
                self.assertNotIn(parent_unit, child["cgroup"])
                self.assertFalse(child["inherited_key"])
                self.assertEqual(child["literal"], "literal$HOME%h")
                subprocess.run(["systemctl", "--user", "stop", parent_unit], env=env, check=True, timeout=15)
                self.assertTrue(Path('/proc', str(child['pid'])).exists())
                active = subprocess.run(["systemctl", "--user", "is-active", app_unit], env=env, capture_output=True, text=True)
                self.assertEqual(active.stdout.strip(), "active")
            finally:
                subprocess.run(["systemctl", "--user", "stop", parent_unit], env=env, capture_output=True, timeout=15)
                if app_unit:
                    subprocess.run(["systemctl", "--user", "stop", app_unit], env=env, capture_output=True, timeout=15)


if __name__ == "__main__":
    unittest.main()
