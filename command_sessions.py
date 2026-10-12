"""Owned shell jobs with incremental, bounded output and explicit cancellation."""
from __future__ import annotations
import atexit
import codecs
import os
import signal
import subprocess
import threading
import time
import uuid
from security import host_environment
from host_execution import launch_host


class Buffer:
    def __init__(self, capacity=262144):
        self.text, self.end, self.capacity = '', 0, capacity
        self.lock = threading.Lock()

    def append(self, text):
        with self.lock:
            self.end += len(text)
            self.text = (self.text + text)[-self.capacity:]

    def read(self, offset=0, limit=64000):
        with self.lock:
            oldest = self.end - len(self.text)
            start = min(self.end, max(oldest, offset))
            value = self.text[start-oldest:start-oldest+limit]
            return value, start+len(value), offset < oldest


class Job:
    def __init__(self, process, timeout, cwd, history=None, receipt=None):
        self.process, self.cwd = process, str(cwd)
        self.history, self.receipt = history, receipt or {'status': 'disabled'}
        self.stdout, self.stderr = Buffer(), Buffer()
        self.done = threading.Event()
        self.signal_lock = threading.Lock()
        self.timed_out = False
        self.cancel_requested = False
        self.cleanup_warning = None
        self.started = time.time()
        self.readers = []
        for pipe, output in ((process.stdout,self.stdout),(process.stderr,self.stderr)):
            t=threading.Thread(target=self._drain,args=(pipe,output),daemon=True)
            t.start(); self.readers.append(t)
        threading.Thread(target=self._wait,args=(timeout,),daemon=True).start()

    @staticmethod
    def _drain(pipe, output):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                chunk = os.read(pipe.fileno(), 8192)
                if not chunk: break
                output.append(decoder.decode(chunk))
            output.append(decoder.decode(b'', final=True))
        finally:
            pipe.close()

    def _signal(self, sig):
        with self.signal_lock:
            # The shell may already have exited while its children still own the
            # pipes. The process group remains ours until job cleanup finishes.
            if not self.done.is_set():
                try:
                    if hasattr(self.process, 'signal_owned'):
                        self.process.signal_owned(sig)
                    else:
                        os.killpg(self.process.pid, sig)
                except ProcessLookupError:
                    pass
                except (OSError, subprocess.TimeoutExpired):
                    self.cleanup_warning = 'Owned process cleanup was not confirmed' 

    def stop(self):
        self.cancel_requested = True
        self._signal(signal.SIGTERM)
        if not self.done.wait(1):
            self._signal(signal.SIGKILL)
        self.done.wait(2)

    def _wait(self, timeout):
        try:
            self.process.wait(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            self._signal(signal.SIGTERM)
            try: self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.cleanup_warning = 'Owned process cleanup was not confirmed'
                    # Stop only our own systemd-run wrapper. Its unit is uncertain;
                    # do not claim successful cleanup or permit command replay.
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=3)
        except Exception:
            self.cleanup_warning = 'Owned job completion was not confirmed; inspect effects before retrying'
        finally:
            # Background descendants must not outlive an owned shell job. Send
            # TERM even if the leader exited, then KILL any group still alive.
            self._signal(signal.SIGTERM)
            for thread in self.readers: thread.join(timeout=.5)
            self._signal(signal.SIGKILL)
            for thread in self.readers: thread.join(timeout=2)
            try:
                if hasattr(self.process, 'cleanup'):
                    try:
                        self.process.cleanup()
                    except Exception:
                        self.cleanup_warning = 'Owned process cleanup was not confirmed'
                if self.history is not None:
                    try:
                        self.history.finish(self.cwd, self.receipt, self.process.poll(), self.timed_out, unknown=bool(self.cleanup_warning))
                    except Exception:
                        self.cleanup_warning = 'Job ended but receipt update failed; do not replay'
            finally:
                self.done.set()

    def read(self, out_offset=0, err_offset=0, limit=64000):
        out, out_cursor, out_lost = self.stdout.read(out_offset, limit)
        err, err_cursor, err_lost = self.stderr.read(err_offset, limit)
        warning = self.cleanup_warning or getattr(self.history, 'warning', None)
        state = ('running' if not self.done.is_set() else 'unknown' if warning else
                 'timed_out' if self.timed_out else 'cancelled' if self.cancel_requested else
                 'completed' if self.process.poll() == 0 else 'failed')
        return {'running':not self.done.is_set(), 'returncode':self.process.poll(),
                'execution_state': state, 'automatic_retry': False, 'receipt_warning': warning,
                'stdout':out,'stderr':err,'stdout_offset':out_cursor,'stderr_offset':err_cursor,
                'stdout_more':out_cursor < self.stdout.end, 'stderr_more':err_cursor < self.stderr.end,
                'output_pending':out_cursor < self.stdout.end or err_cursor < self.stderr.end,
                'output_truncated':out_lost or err_lost, 'timed_out':self.timed_out,
                'cwd':self.cwd,'started_at':self.started, 'history': dict(self.receipt),
                'cleanup_warning': self.cleanup_warning}


class CommandSessions:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.RLock()

    def start(self, workspace, cwd, config, command, timeout=0, history=None, environment=None):
        with self.lock:
            if len(self.jobs) >= 64:
                for key, job in list(self.jobs.items()):
                    if job.done.is_set(): del self.jobs[key]
            if len(self.jobs) >= 64:
                raise ValueError('64 shell jobs are still running; stop one before starting another')
            receipt = history.begin(str(cwd)) if history is not None else None
            try:
                process = launch_host(['/bin/bash', '--noprofile', '--norc', '-c', command],
                                      cwd, environment if environment is not None else host_environment())
            except Exception:
                if history is not None:
                    history.finish(str(cwd), receipt, None, unknown=True)
                raise
            key=uuid.uuid4().hex
            self.jobs[key]=Job(process,timeout,cwd,history,receipt)
            return key

    def get(self, key):
        with self.lock:
            if key not in self.jobs:
                raise ValueError('Unknown command session; sessions belong to this Bridge process')
            return self.jobs[key]

    def list(self):
        with self.lock:
            return [{'session_id':k,'running':not j.done.is_set(),'returncode':j.process.poll(),
                     'cwd':j.cwd,'started_at':j.started} for k,j in self.jobs.items()]

    def close(self):
        with self.lock: jobs=list(self.jobs.values())
        for job in jobs:
            if not job.done.is_set():job.stop()


sessions=CommandSessions()
atexit.register(sessions.close)
