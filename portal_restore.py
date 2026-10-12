"""Private, single-use RemoteDesktop grants for the NOBUBBLEWARP variant.

No token is returned through desktop IPC. A nonblocking lock spans selection to
Start, and the old token is removed durably before it can be sent to the portal.
Only the portal can grant/restore permission; storage failures use normal consent.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import time
import uuid


DIRECTORY = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.local/state/chatgpt-local-bridge-NOBUBBLEWARP/desktop-portal'
TOKEN_FILE = 'remote-desktop.json'


def valid_token(value):
    return isinstance(value, str) and 0 < len(value) <= 4096 and all(32 < ord(c) < 127 for c in value)


class RestoreTokenStore:
    def __init__(self, directory=DIRECTORY):
        self.directory = Path(directory)
        self.directory_fd = None
        self.lock_fd = None

    def _directory(self, create=False):
        if not self.directory.is_absolute() or '..' in self.directory.parts:
            raise ValueError('Invalid portal storage directory')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        private_ancestor = False
        try:
            for component in self.directory.parts[1:]:
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if (info.st_uid not in (0, os.getuid())
                        or (not private_ancestor and info.st_mode & 0o022
                            and not info.st_mode & stat.S_ISVTX)):
                    raise ValueError('Unsafe portal storage directory')
                # An owned 0700 ancestor makes later group bits unreachable to
                # other users. Do not chmod unrelated pre-existing state dirs.
                private_ancestor |= info.st_uid == os.getuid() and not info.st_mode & 0o077
            if os.fstat(fd).st_uid != os.getuid():
                raise ValueError('Portal storage must belong to the current user')
            if create:
                os.fchmod(fd, 0o700)
            elif os.fstat(fd).st_mode & 0o077:
                raise ValueError('Portal storage is not private')
            result, fd = fd, None
            return result
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _private_file(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('Unsafe portal storage file')

    def _read(self, directory_fd):
        try:
            fd = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            self._private_file(fd)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                blob = stream.read(8193)
            if len(blob) > 8192:
                raise ValueError('Invalid saved portal grant')
            try:
                data = json.loads(blob)
            except (ValueError, UnicodeError):
                raise ValueError('Invalid saved portal grant') from None
            if (not isinstance(data, dict) or data.get('version') != 1 or data.get('persist_mode') != 2
                    or data.get('scope') != 'keyboard-pointer-one-monitor-cursor-embedded'
                    or not valid_token(data.get('restore_token'))):
                raise ValueError('Invalid saved portal grant')
            return data['restore_token']
        finally:
            os.close(fd)

    def has_token(self):
        try:
            fd = self._directory()
        except FileNotFoundError:
            return False
        try:
            return self._read(fd) is not None
        finally:
            os.close(fd)

    def begin(self):
        if self.lock_fd is not None:
            raise RuntimeError('Portal grant already reserved')
        self.directory_fd = self._directory(create=True)
        try:
            self.lock_fd = os.open('grant.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                   0o600, dir_fd=self.directory_fd)
            self._private_file(self.lock_fd)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            token = self._read(self.directory_fd)
            if token is not None:
                os.unlink(TOKEN_FILE, dir_fd=self.directory_fd)
                os.fsync(self.directory_fd)
            return token
        except BaseException:
            self.release()
            raise

    def save(self, token):
        if self.lock_fd is None or not valid_token(token):
            raise ValueError('Invalid portal grant transaction')
        data = {'version': 1, 'persist_mode': 2,
                'scope': 'keyboard-pointer-one-monitor-cursor-embedded',
                'restore_token': token, 'updated_at': int(time.time())}
        name = 'grant-' + uuid.uuid4().hex + '.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.directory_fd)
        try:
            with os.fdopen(fd, 'w', closefd=False) as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(fd)
            os.replace(name, TOKEN_FILE, src_dir_fd=self.directory_fd, dst_dir_fd=self.directory_fd)
            os.fsync(self.directory_fd)
        finally:
            os.close(fd)
            try:
                os.unlink(name, dir_fd=self.directory_fd)
            except FileNotFoundError:
                pass

    def release(self):
        for name in ('lock_fd', 'directory_fd'):
            fd = getattr(self, name)
            setattr(self, name, None)
            if fd is not None:
                os.close(fd)


class PortalPersistence:
    """Token lifecycle and safe, token-free capability/status information."""
    def __init__(self, version, store=None):
        self.version = version
        self.store = store if store is not None else RestoreTokenStore()
        self.requested_mode = 0
        self.restore_attempted = False
        self.grant_saved = False
        self.warning = None
        self._secrets = []

    def begin(self, transient=False):
        self.abort()
        self.requested_mode = 0
        self.restore_attempted = self.grant_saved = False
        self.warning = None
        self._secrets = []
        if transient or self.version < 2:
            self.warning = 'portal_rejected_persistence' if transient else 'remote_desktop_version_unsupported'
            return 0, None
        try:
            token = self.store.begin()
        except Exception:
            self.warning = 'private_storage_unavailable_or_busy'
            return 0, None
        self.requested_mode = 2
        self.restore_attempted = token is not None
        if token:
            self._secrets.append(token)
        return 2, token

    def complete(self, data):
        token = data.get('restore_token')
        if valid_token(token):
            self._secrets.append(str(token))
        try:
            if self.requested_mode == 2:
                if valid_token(token) and int(data.get('persist_mode', 2)) == 2:
                    self.store.save(str(token))
                    self.grant_saved = True
                else:
                    self.warning = 'portal_did_not_grant_persistent_permission'
        except Exception:
            self.warning = 'replacement_token_not_saved'
        finally:
            self.abort()

    def abort(self):
        self.store.release()

    def redact(self, message):
        text = str(message)
        for token in self._secrets:
            text = text.replace(token, '[portal token redacted]')
        return text

    def status(self):
        try:
            available = self.store.has_token()
        except Exception:
            available = False
        return {'remote_desktop_version': self.version, 'supported_api': self.version >= 2,
                'requested_mode': self.requested_mode, 'restore_attempted': self.restore_attempted,
                'token_available': available, 'grant_saved': self.grant_saved, 'warning': self.warning,
                'policy': 'GNOME decides whether consent can be restored; tokens are single-use'}
