import asyncio
import sys
import types
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, McpManager


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()


def test_stdio_disconnect_closes_transport_from_lifecycle_task(monkeypatch):
    state = {"entered_task": None, "exited": False}

    class FakeTool:
        name = "fake_tool"
        description = "Fake tool"
        inputSchema = {"type": "object", "properties": {}}

    class FakeToolsResult:
        tools = [FakeTool()]

    class FakeTransport:
        async def __aenter__(self):
            state["entered_task"] = asyncio.current_task()
            return object(), object()

        async def __aexit__(self, exc_type, exc, tb):
            assert asyncio.current_task() is state["entered_task"]
            state["exited"] = True

    class FakeSession:
        def __init__(self, read_stream, write_stream):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return FakeToolsResult()

    class FakeParams:
        def __init__(self, command, args, env):
            self.command = command
            self.args = args
            self.env = env

    fake_mcp = types.ModuleType("mcp")
    fake_mcp.ClientSession = FakeSession
    fake_mcp.StdioServerParameters = FakeParams
    fake_client = types.ModuleType("mcp.client")
    fake_stdio = types.ModuleType("mcp.client.stdio")
    fake_stdio.stdio_client = lambda params: FakeTransport()

    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", fake_client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", fake_stdio)

    async def run():
        mgr = McpManager()
        assert await mgr.connect_server("srv", "Fake", "stdio", command="fake")
        await mgr.disconnect_server("srv")

    asyncio.run(run())
    assert state["exited"] is True
