"""Keep a command's user unit alive; exit when its Bridge owner disappears."""
import subprocess
import sys
from pathlib import Path

from host_execution import process_identity


def main():
    owner, birth, ready, *argv = sys.argv[1:]
    owner = int(owner)
    if not argv or process_identity(owner) != birth:
        return 125
    # Inherited stdio belongs to this call. systemd owns the entire cgroup,
    # including descendants which call setsid() or double-fork.
    child = subprocess.Popen(argv)
    Path(ready).write_text('ready\n')
    while True:
        try:
            code = child.wait(timeout=.1)
            return code if code >= 0 else 128 - code
        except subprocess.TimeoutExpired:
            if process_identity(owner) != birth:
                # Leaving the unit causes KillMode=control-group cleanup.
                return 125


if __name__ == '__main__':
    raise SystemExit(main())
