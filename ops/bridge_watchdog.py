"""Conservative tunnel watchdog: stale polling is separate from process liveness."""
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.request

SOURCE = Path(__file__).resolve().parent.parent
if not (SOURCE / 'operation_store.py').is_file():
    SOURCE = Path('/home/bridge-user/chatgpt-local-bridge-NOBUBBLEWARP')
sys.path.insert(0, str(SOURCE))
from operation_store import OperationStore, configured_directory


def store_from_config():
    config = json.loads((SOURCE / 'bridge_config.json').read_text())
    return OperationStore(configured_directory(config))


def poll_health(now=None):
    now = time.time() if now is None else now
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8090/metrics', timeout=2) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('metric limit')
        values = re.findall(r'^commands_poll_last_successful_timestamp_seconds(?:\{[^\n]*\})?\s+(\S+)', raw.decode(), re.M)
        last = max(float(v) for v in values)
        if not math.isfinite(last) or last <= 0 or last > now + 30:
            raise ValueError('invalid timestamp')
        return {'remote_connected': now - last <= 180, 'last_success_at': last,
                'last_success_age_seconds': max(0, round(now-last, 1)), 'probe_error': None}
    except Exception:
        return {'remote_connected': None, 'last_success_at': None,
                'last_success_age_seconds': None, 'probe_error': 'poll_metrics_unavailable'}


def read_status(store):
    try:
        result = json.loads((store.directory / 'watchdog.json').read_text())
        result.pop('recovery_times', None)
        if time.time() - result.get('last_checked_at', 0) > 35:
            result.update(recovery_state='monitor_stale', remote_connected=None)
        return result
    except (OSError, ValueError):
        return {'recovery_state': 'not_observed', 'remote_connected': None}


class Watchdog:
    def __init__(self, store, now=None):
        self.store = store
        self.started = time.monotonic() if now is None else now
        self.failures = 0
        self.recovery_fd = None
        self.history_valid = True
        try:
            prior = json.loads((store.directory / 'watchdog.json').read_text())
            if prior.get('history_valid') is False:
                raise ValueError('Untrusted recovery history')
            self.recoveries = prior['recovery_times']
            if not isinstance(self.recoveries, list) or any(type(t) not in (int,float) or not math.isfinite(t) for t in self.recoveries):
                raise ValueError('invalid recovery history')
        except FileNotFoundError:
            self.recoveries = []
        except (OSError, ValueError, KeyError):
            self.recoveries = []
            self.history_valid = False

    def save(self, result):
        self.store.prepare()
        fd, temporary = tempfile.mkstemp(prefix='.watchdog-', dir=self.store.directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(result, recovery_times=self.recoveries, history_valid=self.history_valid), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.store.directory / 'watchdog.json')
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def tick(self, health, process_alive, mcp_alive, *, now=None, monotonic=None):
        now = time.time() if now is None else now
        monotonic = time.monotonic() if monotonic is None else monotonic
        healthy = process_alive and mcp_alive and health['remote_connected'] is True
        self.failures = 0 if healthy else self.failures + 1
        result = dict(health, process_alive=process_alive, mcp_process_present=mcp_alive,
                      last_checked_at=now, consecutive_failures=self.failures,
                      recovery_state='healthy' if healthy else 'degraded', blockers={})
        restart = False
        if not healthy:
            if monotonic - self.started < 180:
                result['recovery_state'] = 'startup_grace'
            elif self.failures < 3:
                result['recovery_state'] = 'confirming_failure'
            elif not self.history_valid:
                result['recovery_state'] = 'invalid_recovery_history'
            else:
                self.recoveries = [t for t in self.recoveries if now - t < 600]
                if len(self.recoveries) >= 2 or (self.recoveries and now-self.recoveries[-1] < 180):
                    result['recovery_state'] = 'rate_limited'
                else:
                    try:
                        self.recovery_fd = self.store.gate(exclusive=True)
                        blockers = self.store.transport_blockers()
                        result['blockers'] = blockers
                        if blockers:
                            result['recovery_state'] = 'deferred_unresolved_commands'
                        else:
                            result['recovery_state'] = 'restarting'
                            self.recoveries.append(now)
                            restart = True
                    except BlockingIOError:
                        result['recovery_state'] = 'deferred_active_commands'
                    except Exception:
                        result['recovery_state'] = 'operational_storage_unavailable'
        try:
            # Persist the recovery budget BEFORE asking systemd for a restart.
            self.save(result)
        except Exception:
            restart = False
            result['recovery_state'] = 'status_persistence_failed'
        if not restart:
            self.close()
        return result, restart

    def close(self):
        if self.recovery_fd is not None:
            os.close(self.recovery_fd)
            self.recovery_fd = None
