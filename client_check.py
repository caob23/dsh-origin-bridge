"""Compatibility check with the official MCP SDK client.

smoke_test.py speaks raw JSON-RPC; this one proves a stock MCP client — the same
protocol dsh's @deepseek-ai/dsh-mcp-client speaks — can list and call our tools.

    pip install "mcp>=1.0"
    python client_check.py
"""

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-u", os.path.join(HERE, "server.py")],
        cwd=HERE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server      :", init.server_info.name, init.server_info.version)
            print("protocol    :", init.protocol_version)

            tools = (await session.list_tools()).tools
            print("tools       :", len(tools))
            for t in tools:
                print("   %-20s %s" % (t.name, t.description.split("。")[0][:60]))

            res = await session.call_tool("origin_status", {})
            text = "".join(c.text for c in res.content if c.type == "text")
            payload = json.loads(text)
            print("status call :", "connected" if payload.get("connected") else payload)

            if "--figure" in sys.argv:
                out = os.path.join(HERE, "out")
                os.makedirs(out, exist_ok=True)
                res = await session.call_tool("origin_figure", {
                    "source": os.path.join(HERE, "sample.dat"),
                    "x_title": "Time (s)", "y_title": "Signal (mV)",
                    "output_dir": out, "width": 1400,
                })
                payload = json.loads("".join(c.text for c in res.content if c.type == "text"))
                if not payload.get("ok"):
                    print("figure FAILED:", json.dumps(payload, ensure_ascii=False)[:900])
                else:
                    print("figure      :", payload.get("ok"), payload.get("delivered"),
                          payload.get("duration_ms"), "ms")


if __name__ == "__main__":
    asyncio.run(main())
