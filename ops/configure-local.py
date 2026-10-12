#!/usr/bin/env python3
"""Explicit setup from a public template; never overwrite a live configuration."""
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parent.parent
source=root/'bridge_config.example.json';destination=root/'bridge_config.json'
if destination.exists():raise SystemExit('bridge_config.json already exists; review it manually. Nothing changed.')
data=json.loads(source.read_text())
def expand(value):
    if isinstance(value,str):return value.replace('/home/bridge-user',str(Path.home()))
    if isinstance(value,list):return [expand(x) for x in value]
    if isinstance(value,dict):return {k:expand(v) for k,v in value.items()}
    return value
with destination.open('x') as out:json.dump(expand(data),out,indent=2);out.write('\n')
os.chmod(destination,0o600)
print('Created private bridge_config.json from the example. Review it before launching. No service started.')
