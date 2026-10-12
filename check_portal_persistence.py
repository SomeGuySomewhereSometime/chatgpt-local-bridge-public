"""Opt-in live acceptance. GNOME consent is mandatory when the portal asks.

No input is sent. Saves only the normal private grant, never prints tokens or
screen pixels. Two separate helper processes verify restore and capture reuse;
this does not reboot the machine and cannot certify persistence across reboot.
"""
import argparse
import base64
import json
import time

from desktop import DesktopClient


def wait_active(client, timeout):
    status = client.call('session', command='start')
    deadline = time.monotonic() + timeout
    print('SESSION_START', json.dumps(status), flush=True)
    while status['state'] == 'pending' and time.monotonic() < deadline:
        time.sleep(.5)
        status = client.call('session', command='status')
    if status['state'] != 'active':
        raise RuntimeError('Session not authorized: ' + json.dumps(status))
    print('SESSION_ACTIVE', json.dumps(status), flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Request real GNOME consent and save a grant')
    parser.add_argument('--timeout', type=int, default=90)
    args = parser.parse_args()
    if not args.live:
        parser.error('Explicit --live is required; this may present GNOME consent')
    first = DesktopClient()
    try:
        status = wait_active(first, args.timeout)
        frame = first.capture_existing()
        image = frame['image']
        blob = base64.b64decode(image['data'], validate=True)
        assert blob.startswith(b'\x89PNG\r\n\x1a\n') and len(blob) > 24
        print('AUTHORIZED_CAPTURE_PASS', image['width'], image['height'], len(blob), flush=True)
        if not status['persistence']['grant_saved']:
            print('GNOME_PERSISTENCE_NOT_GRANTED: current consent/capture work; fresh consent may be needed', flush=True)
            return
        first.call('session', command='stop')
    finally:
        first.close()
    second = DesktopClient()
    try:
        status = wait_active(second, args.timeout)
        assert status['persistence']['restore_attempted'], status
        assert status['persistence']['grant_saved'], status
        frame = second.capture_existing()
        assert frame['available'] and frame['image']['session_id'] == status['session_id']
        print('NEW_WORKER_RESTORE_AND_ROTATION_PASS: GNOME controls whether a dialog was shown', flush=True)
        second.call('session', command='stop')
    finally:
        second.close()


if __name__ == '__main__':
    main()
