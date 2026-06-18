import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "servermcp" / "server.py"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


async def main() -> None:
    python_executable = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)

    server_params = StdioServerParameters(
        command=str(python_executable),
        args=[str(SERVER_SCRIPT)],
        cwd=str(PROJECT_ROOT),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("available tools:", [tool.name for tool in tools.tools])

            result = await session.call_tool("hello_world", {})
            text = result.content[0].text if result.content else ""
            print("hello_world result:", text)


if __name__ == "__main__":
    asyncio.run(main())
