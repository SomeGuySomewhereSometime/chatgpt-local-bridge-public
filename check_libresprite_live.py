"""One read-only real MCP call. Never replay on an uncertain outcome."""
import asyncio
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client('http://127.0.0.1:9091/mcp',timeout=30) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            names=[tool.name for tool in (await session.list_tools()).tools]
            assert names==['run_script'],names
            resources=[str(resource.uri) for resource in (await session.list_resources()).resources]
            assert {'docs://reference','docs://examples'} <= set(resources)
            result=await session.call_tool('run_script',{'script':'console.log("MCP_TEST_VERSION=" + app.version)'})
            output=''.join(c.text for c in result.content if hasattr(c,'text'))
            assert not result.isError and 'MCP_TEST_VERSION=1.1-dev' in output, output
            print(json.dumps({'ok':True,'tools':names,'resources':resources,'output':output}))
if __name__=='__main__':
    asyncio.run(main())
