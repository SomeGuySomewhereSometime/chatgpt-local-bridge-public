"""Read the actual Desktop catalogue in a separate process (no mixed test server)."""
import asyncio
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


async def desktop_tools():
    process=await asyncio.create_subprocess_exec(sys.executable,'-B','-c',
        'import asyncio,json,mcp_server; print(json.dumps([x.model_dump() for x in asyncio.run(mcp_server.mcp.list_tools())]))',
        cwd=Path(__file__).parent,env=dict(os.environ,BRIDGE_PROFILE='desktop',BRIDGE_MEMORY_DISABLED='1'),
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await process.communicate()
    if process.returncode:raise RuntimeError(err.decode())
    return [SimpleNamespace(**item) for item in json.loads(out)]
