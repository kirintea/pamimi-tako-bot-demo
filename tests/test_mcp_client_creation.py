# -*- coding: utf-8 -*-

"""MCP 客户端创建测试 — AgentFactory._create_mcp_clients

覆盖：
- http / streamable_http / sse / streamableHttp 各传输别名均应产出 HttpMCPConfig 客户端
- stdio 需 command，HTTP 族需 url，缺失时跳过（不得静默丢弃无日志）
- stdio 的 env（UI 暂存于 headers）应传入 StdioMCPConfig.env
- validate_connection_fields：拒绝空壳记录（连接字段缺失）
"""

import pytest

from agentscope.mcp import HttpMCPConfig, StdioMCPConfig

from core.agent.factory import AgentFactory
from core.config.schemas import MCPConfig
from core.mcp.config_store import validate_connection_fields


class TestHttpFamilyTransports:
    @pytest.mark.parametrize(
        "transport",
        ["http", "https", "sse", "streamable_http", "streamableHttp", "streamable-http"],
    )
    def test_http_family_creates_client(self, transport):
        cfg = MCPConfig(name="svc", transport=transport, url="http://x/mcp")
        clients = AgentFactory._create_mcp_clients([cfg])
        assert len(clients) == 1, f"transport={transport} 应创建 MCP 客户端"
        assert isinstance(clients[0].mcp_config, HttpMCPConfig)
        assert clients[0].mcp_config.url == "http://x/mcp"

    def test_http_headers_filtered_and_passed(self):
        cfg = MCPConfig(
            name="svc",
            transport="streamable_http",
            url="http://x/mcp",
            headers={"Authorization": "Bearer t", "X-Empty": ""},
        )
        clients = AgentFactory._create_mcp_clients([cfg])
        assert clients[0].mcp_config.headers == {"Authorization": "Bearer t"}

    def test_http_without_url_is_skipped(self):
        cfg = MCPConfig(name="svc", transport="streamable_http")
        assert AgentFactory._create_mcp_clients([cfg]) == []


class TestStdioTransport:
    def test_stdio_with_command_creates_stateful_client(self):
        cfg = MCPConfig(name="svc", transport="stdio", command="npx", args=["-y", "pkg"])
        clients = AgentFactory._create_mcp_clients([cfg])
        assert len(clients) == 1
        assert isinstance(clients[0].mcp_config, StdioMCPConfig)
        assert clients[0].is_stateful is True
        assert clients[0].mcp_config.command == "npx"
        assert clients[0].mcp_config.args == ["-y", "pkg"]

    def test_stdio_env_from_headers_is_passed(self):
        """UI 将 stdio 环境变量暂存于 headers，应传入 StdioMCPConfig.env。"""
        cfg = MCPConfig(
            name="svc",
            transport="stdio",
            command="npx",
            headers={"API_KEY": "xxx", "EMPTY": ""},
        )
        clients = AgentFactory._create_mcp_clients([cfg])
        assert clients[0].mcp_config.env == {"API_KEY": "xxx"}

    def test_stdio_without_command_is_skipped(self):
        cfg = MCPConfig(name="svc", transport="stdio")
        assert AgentFactory._create_mcp_clients([cfg]) == []


class TestSkippedEntriesAreVisible:
    def test_unknown_transport_skipped(self):
        cfg = MCPConfig(name="svc", transport="carrier-pigeon", url="http://x/mcp")
        assert AgentFactory._create_mcp_clients([cfg]) == []

    def test_skip_writes_warning_log(self):
        """跳过的条目必须留下日志，避免运行期静默 0 客户端。

        loguru 不向 stdlib logging 传播，故用 loguru sink 捕获。
        """
        from loguru import logger

        records: list[str] = []
        sink_id = logger.add(lambda m: records.append(str(m)), level="WARNING")
        try:
            AgentFactory._create_mcp_clients([MCPConfig(name="broken", transport="stdio")])
        finally:
            logger.remove(sink_id)
        assert any("broken" in r for r in records), (
            f"跳过条目应记录警告日志，实际日志: {records}"
        )


class TestStatefulMcpConnection:
    """有状态（stdio）MCP 客户端必须在 Toolkit 构造前 connect()

    agentscope Toolkit.__init__ 对 is_stateful 且未连接的客户端直接抛
    ValueError，会让整个 Agent 创建失败。"""

    pytestmark = pytest.mark.asyncio(loop_scope="session")

    def _stdio_cfg(self, **kw):
        base = dict(name="svc", transport="stdio", command="npx", args=["-y", "pkg"])
        base.update(kw)
        return MCPConfig(**base)

    async def test_toolkit_connects_stateful_clients(self, monkeypatch):
        from core.agent.factory import AgentFactory

        connected: list[str] = []

        async def _fake_connect(self):
            connected.append(self.name)
            self._is_connected = True

        monkeypatch.setattr("agentscope.mcp.MCPClient.connect", _fake_connect)
        await AgentFactory._create_toolkit(
            _minimal_config(),
            user_id=None,
            agent_space_dir=None,
            mcp_configs=[self._stdio_cfg()],
        )
        assert connected == ["svc"], "有状态客户端应在 Toolkit 构造前 connect()"

    async def test_connect_failure_drops_client_but_keeps_toolkit(self, monkeypatch):
        """连不上的 stdio MCP 不应拖垮整个 Agent 创建。"""
        from core.agent.factory import AgentFactory

        async def _boom(self):
            raise RuntimeError("spawn failed")

        monkeypatch.setattr("agentscope.mcp.MCPClient.connect", _boom)
        toolkit = await AgentFactory._create_toolkit(
            _minimal_config(),
            user_id=None,
            agent_space_dir=None,
            mcp_configs=[self._stdio_cfg(), MCPConfig(
                name="ok-http", transport="http", url="http://x/mcp",
            )],
        )
        names = [c.name for g in toolkit.tool_groups for c in g.mcps]
        assert names == ["ok-http"], f"连接失败的客户端应被剔除，实际: {names}"

    async def test_connect_timeout_drops_client(self, monkeypatch):
        """无响应的 stdio MCP 不应无限期阻塞 Agent 创建。"""
        import asyncio as _asyncio

        from core.agent import factory as factory_mod

        monkeypatch.setattr(factory_mod, "MCP_CONNECT_TIMEOUT_SECONDS", 0.05)

        async def _hang(self):
            await _asyncio.sleep(30)

        monkeypatch.setattr("agentscope.mcp.MCPClient.connect", _hang)
        toolkit = await AgentFactory._create_toolkit(
            _minimal_config(),
            user_id=None,
            agent_space_dir=None,
            mcp_configs=[self._stdio_cfg()],
        )
        names = [c.name for g in toolkit.tool_groups for c in g.mcps]
        assert names == [], f"超时的客户端应被剔除，实际: {names}"

    async def test_close_mcp_clients_closes_stateful_only(self, monkeypatch):
        from core.agent.factory import AgentFactory

        closed: list[str] = []

        async def _fake_connect(self):
            self._is_connected = True

        async def _fake_close(self, ignore_errors=True):
            closed.append(self.name)

        monkeypatch.setattr("agentscope.mcp.MCPClient.connect", _fake_connect)
        monkeypatch.setattr("agentscope.mcp.MCPClient.close", _fake_close)
        toolkit = await AgentFactory._create_toolkit(
            _minimal_config(),
            user_id=None,
            agent_space_dir=None,
            mcp_configs=[
                self._stdio_cfg(),
                MCPConfig(name="ok-http", transport="http", url="http://x/mcp"),
            ],
        )

        class _Agent:
            pass

        agent = _Agent()
        agent.toolkit = toolkit
        await AgentFactory.close_mcp_clients(agent)
        assert closed == ["svc"], f"仅应关闭有状态客户端，实际: {closed}"


def _minimal_config():
    """构造最小 AppConfig（MCP 配置经 mcp_configs= 参数注入，不走 YAML）。"""
    from core.config.schemas import AppConfig

    return AppConfig.model_validate({
        "otel": {"endpoint": "http://localhost:4317", "environment": "development"},
        "llm": {"api_key": "sk-test", "base_url": "http://localhost:9/v1", "model": "m"},
    })


class TestValidateConnectionFields:
    """API 侧完整性校验 — 阻止只存名字的空壳记录"""

    def test_stdio_with_command_ok(self):
        assert validate_connection_fields("stdio", command="npx") is None

    def test_stdio_without_command_rejected(self):
        assert validate_connection_fields("stdio", command=None) is not None
        assert validate_connection_fields("stdio", command="  ") is not None

    @pytest.mark.parametrize(
        "transport",
        ["http", "https", "sse", "streamable_http", "streamableHttp", "streamable-http"],
    )
    def test_http_family_with_url_ok(self, transport):
        assert validate_connection_fields(transport, url="http://x/mcp") is None

    def test_http_family_without_url_rejected(self):
        assert validate_connection_fields("streamable_http", url=None) is not None
        assert validate_connection_fields("sse", url="") is not None

    def test_unknown_transport_rejected(self):
        assert validate_connection_fields("carrier-pigeon", url="http://x") is not None

    def test_transport_aliasing(self):
        """大小写 / 连字符别名应与工厂判定一致。"""
        assert validate_connection_fields("StreamableHTTP", url="http://x/mcp") is None
        assert validate_connection_fields("stdio ", command="npx") is None
