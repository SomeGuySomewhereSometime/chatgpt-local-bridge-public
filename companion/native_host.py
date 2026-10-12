#!/usr/bin/python3
"""Chrome native messaging host: one bounded JSON request, one response."""
import json
from pathlib import Path
import struct
import sys
from coordinator import Coordinator


def main():
    manifest=json.loads((Path(__file__).parent/'extension/manifest.json').read_text())
    import base64, hashlib
    digest=hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension_id=''.join(chr(97+int(c,16)) for c in digest)
    if len(sys.argv)<2 or sys.argv[1] != f'chrome-extension://{extension_id}/':
        return 1
    raw=sys.stdin.buffer.read(4)
    if len(raw)!=4: return 1
    length=struct.unpack('<I',raw)[0]
    if length>65536: return 1
    try:
        payload=json.loads(sys.stdin.buffer.read(length))
        if payload.get('action')=='list':
            # Listing is local panel only; extension cannot enumerate other conversations.
            raise ValueError('This action is not available in the extension.')
        if payload.get('action')=='panel_list':
            if not isinstance(payload.get('client'),str) or not payload['client']: raise ValueError('Missing panel client.')
            payload={ 'action':'list', 'client':payload['client'] }
        result={'ok':True,**Coordinator().dispatch(payload)}
    except Exception as exc:
        result={'ok':False,'error':str(exc)[:250]}
    data=json.dumps(result,ensure_ascii=False).encode()
    sys.stdout.buffer.write(struct.pack('<I',len(data))+data)
    sys.stdout.buffer.flush()
    return 0

if __name__=='__main__': sys.exit(main())
