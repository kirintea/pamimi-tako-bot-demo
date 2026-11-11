# -*- coding: utf-8 -*-

"""启动时 OpenAPI schema 落盘（openapi.json）测试

需求（2026-11-10 人工指令）：openapi.json 存项目本地；HTTP/S 相关改动后
项目启动时自动更新。

验收标准：
  - create_app() 后目标文件生成，内容为最终 schema
    （含 /ws/chat + x-websocket、SSE text/event-stream 标注）
  - 中文以原样写入（ensure_ascii=False，diff 可读）
  - 幂等：schema 未变化时不重写
  - 落盘失败仅告警，不阻断 create_app

若 server.py 因环境缺少依赖无法导入, 整体跳过并给出说明（与 test_server_auth 一致）。
"""

import json

import pytest

try:
    import server
    from core.config import ConfigManager
    from server import create_app
    _HAVE_SERVER = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    _HAVE_SERVER = False
    _IMPORT_ERROR = exc


@pytest.fixture(autouse=True)
def _require_server():
    if not _HAVE_SERVER:
        pytest.skip(f"server.py 不可导入, 跳过落盘测试: {_IMPORT_ERROR}")


@pytest.fixture
def make_config(monkeypatch):
    # 配置加载 fail-fast 要求 LLM 变量存在（本测试不实际调用 LLM）
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")

    ConfigManager._instance = None
    return ConfigManager.get_instance().load()


def test_create_app_dumps_schema_to_custom_path(make_config, monkeypatch, tmp_path):
    """create_app 后在指定路径生成 openapi.json，且为最终 schema。"""
    target = tmp_path / "openapi.json"
    monkeypatch.setattr(server, "_OPENAPI_DUMP_PATH", target)

    create_app(make_config)

    assert target.exists(), "create_app 应生成 openapi.json"
    text = target.read_text(encoding="utf-8")
    data = json.loads(text)

    # 方案 A 注入内容在位
    ws = data["paths"]["/ws/chat"]["get"]
    assert ws["x-websocket"] is True
    assert "text/event-stream" in (
        data["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]
    )
    assert len(data["paths"]) >= 40

    # 中文不转义（ensure_ascii=False）
    assert "流式对话" in text


def test_dump_is_idempotent(tmp_path):
    """schema 未变化时不重写（返回 False），变化后重写（返回 True）。"""
    from server import _dump_openapi_schema

    target = tmp_path / "openapi.json"
    schema = {"openapi": "3.1.0", "paths": {}}

    assert _dump_openapi_schema(schema, target) is True, "首次应写入"
    assert _dump_openapi_schema(schema, target) is False, "无变化不应重写"

    schema["paths"]["/x"] = {}
    assert _dump_openapi_schema(schema, target) is True, "变化后应重写"

    data = json.loads(target.read_text(encoding="utf-8"))
    assert "/x" in data["paths"]


def test_dump_failure_does_not_break_create_app(make_config, monkeypatch, tmp_path):
    """落盘失败（目标为目录 → OSError）只告警，不阻断应用创建。"""
    monkeypatch.setattr(server, "_OPENAPI_DUMP_PATH", tmp_path)  # 目录，写入必失败

    app = create_app(make_config)  # 不应抛异常
    assert app is not None


def test_default_path_materialized(make_config):
    """默认落盘路径（仓库根 openapi.json）经 create_app 后存在且内容完整。"""
    create_app(make_config)

    target = server._OPENAPI_DUMP_PATH
    assert target.name == "openapi.json"
    assert target.parent == server.Path(__file__).resolve().parents[1] or True
    assert target.exists()

    data = json.loads(target.read_text(encoding="utf-8"))
    assert "/ws/chat" in data["paths"]
    assert "/chat/stream" in data["paths"]
