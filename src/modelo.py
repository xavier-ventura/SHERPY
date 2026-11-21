import asyncio, json, os
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

load_dotenv()

async def main():
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {os.environ['MCP_TOKEN']}"})
    async with streamable_http_client(os.environ["MCP_URL"], http_client=http) as (read, write, *_):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            r = await mcp.call_tool("get_case", {"caseId": "samantha-lewis", "part": "financial-health", "section": "narrative"})
            print(json.loads(r.content[0].text)["value"]["summary_main"])

asyncio.run(main())