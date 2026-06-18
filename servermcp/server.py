import sys

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("hello-world")


@mcp.tool()
def hello_world() -> str:
    """Print and return a hello world message."""
    message = "Hello, world!"
    print(message, file=sys.stderr)
    return message


if __name__ == "__main__":
    mcp.run()
