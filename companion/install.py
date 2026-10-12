"""Register only the native host. Load the extension explicitly in Chrome."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

root=Path(__file__).resolve().parent
m=json.loads((root/'extension/manifest.json').read_text())
digest=hashlib.sha256(base64.b64decode(m['key'])).hexdigest()[:32]
eid=''.join(chr(97+int(c,16)) for c in digest)
directory=Path.home()/'.config/google-chrome/NativeMessagingHosts'
directory.mkdir(parents=True,exist_ok=True)
p=directory/'io.local.bridge_companion.json'
if p.exists(): shutil.copy2(p,p.with_suffix('.backup-'+str(time.time_ns())))
p.write_text(json.dumps({'name':'io.local.bridge_companion','description':'Local Bridge continuation coordinator','path':str(root/'native_host.py'),'type':'stdio','allowed_origins':[f'chrome-extension://{eid}/']},indent=2))
os.chmod(p,0o600)
os.chmod(root/'native_host.py',0o700)
print('Native host registered. Extension ID:',eid)
print('Load unpacked:',root/'extension')
