"""Private storage, single-use rotation, crash/fallback and concurrency regression."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portal_restore import PortalPersistence, RestoreTokenStore, TOKEN_FILE


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'private' / 'desktop-portal'
        self.store = RestoreTokenStore(self.directory)
        self.addCleanup(self.store.release)

    def seed(self, token='first-grant'):
        self.assertIsNone(self.store.begin())
        self.store.save(token)
        self.store.release()

    def test_recreated_process_reads_and_rotates_single_use_grant(self):
        p = PortalPersistence(2, self.store)
        self.assertEqual(p.begin(), (2, None))
        p.complete({'restore_token': 'first-grant'})
        p = PortalPersistence(2, RestoreTokenStore(self.directory))
        self.assertEqual(p.begin(), (2, 'first-grant'))
        self.assertFalse((self.directory / TOKEN_FILE).exists())
        p.complete({'restore_token': 'replacement-grant'})
        again = RestoreTokenStore(self.directory)
        try:
            self.assertEqual(again.begin(), 'replacement-grant')
        finally:
            again.release()

    def test_restrictive_permissions_and_atomic_write(self):
        with patch('os.replace', wraps=os.replace) as replace:
            self.seed()
        self.assertTrue(replace.called)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        for name in (TOKEN_FILE, 'grant.lock'):
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_pending_sessions_never_consume_or_overwrite_same_token(self):
        self.seed()
        p = PortalPersistence(2, self.store)
        self.assertEqual(p.begin(), (2, 'first-grant'))
        other = PortalPersistence(2, RestoreTokenStore(self.directory))
        self.assertEqual(other.begin(), (0, None))
        other.complete({'restore_token': 'must-not-overwrite'})
        p.complete({'restore_token': 'replacement'})
        self.assertTrue(self.store.has_token())
        self.assertNotIn('must-not-overwrite', (self.directory / TOKEN_FILE).read_text())

    def test_crash_or_cancel_does_not_replay_old_token(self):
        self.seed()
        p = PortalPersistence(2, self.store)
        self.assertEqual(p.begin(), (2, 'first-grant'))
        p.abort()
        self.assertEqual(p.begin(), (2, None))
        p.abort()

    def test_missing_token_or_downgraded_permission_uses_consent_next_time(self):
        for response in ({}, {'restore_token': 'transient-only', 'persist_mode': 1},
                         {'restore_token': 'bad\ntoken'}):
            self.seed()
            p = PortalPersistence(2, self.store)
            p.begin()
            p.complete(response)
            self.assertFalse(self.store.has_token())
            self.assertFalse(p.status()['grant_saved'])
            self.assertEqual(p.status()['warning'], 'portal_did_not_grant_persistent_permission')

    def test_unavailable_storage_unsupported_api_and_fallback_do_not_block_consent(self):
        p = PortalPersistence(1, self.store)
        self.assertEqual(p.begin(), (0, None))
        self.assertFalse(self.directory.exists())
        p = PortalPersistence(2, self.store)
        with patch.object(self.store, 'begin', side_effect=OSError('contains-a-secret')):
            self.assertEqual(p.begin(), (0, None))
            self.assertNotIn('contains-a-secret', json.dumps(p.status()))
        self.assertEqual(p.begin(transient=True), (0, None))

    def test_store_failure_keeps_permission_active_but_reports_no_saved_grant(self):
        p = PortalPersistence(2, self.store)
        p.begin()
        with patch.object(self.store, 'save', side_effect=OSError('secret-new-token')):
            p.complete({'restore_token': 'secret-new-token'})
        self.assertFalse(p.status()['grant_saved'])
        self.assertEqual(p.status()['warning'], 'replacement_token_not_saved')
        self.assertIsNone(self.store.lock_fd)

    def test_token_absent_from_status_and_redacted_from_errors(self):
        self.seed('secret-old-token')
        p = PortalPersistence(2, self.store)
        p.begin()
        p.complete({'restore_token': 'secret-new-token'})
        self.assertNotIn('secret-', json.dumps(p.status()))
        self.assertNotIn('secret-', p.redact('secret-old-token and secret-new-token'))

    def test_status_has_no_storage_side_effects(self):
        self.assertFalse(PortalPersistence(2, self.store).status()['token_available'])
        self.assertFalse(self.directory.exists())

    def test_group_bits_below_private_ancestor_do_not_disable_persistence(self):
        parent = self.directory.parent
        parent.mkdir()
        parent.chmod(0o775)  # Protected by TemporaryDirectory's owned 0700 root.
        self.seed()
        self.assertTrue(self.store.has_token())
        self.assertEqual(parent.stat().st_mode & 0o777, 0o775)

    def test_corrupt_oversized_and_public_files_are_not_used(self):
        for blob in ('{broken', 'x' * 8193, '{"version":1,"restore_token":"secret"}'):
            self.seed()
            (self.directory / TOKEN_FILE).write_text(blob)
            with self.assertRaises(ValueError):
                self.store.begin()
            self.assertIsNone(self.store.lock_fd)
            (self.directory / TOKEN_FILE).unlink()
        self.seed()
        (self.directory / TOKEN_FILE).chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.begin()

    def test_symlink_directory_token_lock_and_hardlinks_are_rejected(self):
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        target = elsewhere / 'secret'
        target.write_text('preserved')
        target.chmod(0o600)
        linked = Path(self.tmp.name) / 'link'
        linked.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(OSError):
            RestoreTokenStore(linked / 'portal').begin()
        self.store.begin(); self.store.release()
        for name in (TOKEN_FILE, 'grant.lock'):
            path = self.directory / name
            path.unlink(missing_ok=True)
            path.symlink_to(target)
            with self.assertRaises(OSError):
                self.store.begin()
            path.unlink()
        os.link(target, self.directory / TOKEN_FILE)
        with self.assertRaises(ValueError):
            self.store.begin()
        self.assertEqual(target.read_text(), 'preserved')

    def test_atomic_save_failure_does_not_leave_temporary_token(self):
        self.store.begin()
        with patch('os.replace', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                self.store.save('new-grant')
        self.assertEqual(list(self.directory.glob('*.tmp')), [])
        self.assertFalse(self.store.has_token())


if __name__ == '__main__':
    unittest.main()
