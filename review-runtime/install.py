#!/usr/bin/env python3
"""Install only the pinned official Linux x64 reviewer runtime after checksum verification."""
import hashlib
import json
import os
from pathlib import Path
import platform
import tarfile
import tempfile
import urllib.request

root=Path(__file__).resolve().parent;release=json.loads((root/'release.json').read_text());destination=root/'opencode'
if platform.system()!='Linux' or platform.machine() not in {'x86_64','amd64'}:raise SystemExit('This pinned runtime supports Linux x64 only; choose and verify an official release for your platform.')
if destination.exists():raise SystemExit('opencode already exists; inspect its version/checksum before replacing it.')
with tempfile.TemporaryDirectory(prefix='bridge-review-runtime-') as tmp:
 archive=Path(tmp)/'release.tar.gz'
 with urllib.request.urlopen(release['url'],timeout=60) as stream,archive.open('wb') as out:
  while data:=stream.read(1024*1024):out.write(data)
 if hashlib.sha256(archive.read_bytes()).hexdigest()!=release['sha256']:raise SystemExit('Archive checksum mismatch; no runtime installed.')
 with tarfile.open(archive) as tar:
  members=[m for m in tar.getmembers() if m.isfile() and Path(m.name).name=='opencode']
  if len(members)!=1:raise SystemExit('Unexpected archive layout; no runtime installed.')
  data=tar.extractfile(members[0]).read()
 if hashlib.sha256(data).hexdigest()!=release['binary_sha256']:raise SystemExit('Binary checksum mismatch; no runtime installed.')
 with destination.open('xb') as out:out.write(data)
 os.chmod(destination,0o700)
print('Installed verified official OpenCode '+release['version']+'. No model request was sent.')
