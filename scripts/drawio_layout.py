#!/usr/bin/env python
"""架构图布局工具：PCB 式通道布线 + 几何校验 + PNG 预览 + 生成 .drawio。

用法:
    .venv/Scripts/python.exe scripts/drawio_layout.py

产出:
    docs/Agent开发架构-v2.drawio      4 页架构图（方块大间距 + 显式走线）
    %TEMP%/drawio_layout_check/*.png  每页渲染预览

布线规则（像电路板）:
    - 方块之间留出 >=100px 的走线通道，A* 只在通道网格里走线
    - 任何线段不得穿过无关方块（起点/终点及其祖先容器除外）
    - 两条线禁止共用同一走廊（同向/反向叠压一律拒绝），只允许垂直交叉且加代价
"""

import heapq
import math
import os
import sys
import tempfile
from collections import defaultdict

# ---------------------------------------------------------------------------
# 基础数据结构
# ---------------------------------------------------------------------------

GRID = 10


def N(nid, label, parent, x, y, w, h, kind="leaf"):
    return dict(id=nid, label=label, parent=parent, x=x, y=y, w=w, h=h, kind=kind)


def E(eid, src, dst, label="", style="solid"):
    return dict(id=eid, src=src, dst=dst, label=label, style=style)


KIND_STYLE = {
    "zone": "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;fillColor=#FFF2CC;strokeColor=#D6B656;fontSize=14;fontStyle=1;container=1;pointerEvents=0;",
    "group": "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;fillColor=#F8CECC;strokeColor=#B85450;fontSize=12;fontStyle=1;container=1;pointerEvents=0;",
    "leaf": "rounded=1;whiteSpace=wrap;html=1;fillColor=#ECECFF;strokeColor=#9370DB;fontSize=11;",
    "cyl": "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;fillColor=#ECECFF;strokeColor=#9370DB;fontSize=11;",
    "reserved": "rounded=1;whiteSpace=wrap;html=1;dashed=1;fillColor=#F5F5F5;strokeColor=#999999;fontColor=#999999;fontSize=11;",
    "note": "rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;fillColor=#FFFDE7;strokeColor=#D6B656;dashed=1;fontSize=11;",
}

# 连线调色板（build_drawio 与 render_png 共用，保证预览配色 = 成品配色）
EDGE_PALETTE = ["#2563EB", "#DC2626", "#059669", "#D97706", "#7C3AED", "#DB2777", "#0891B2", "#65A30D"]


def edge_colors(edge_ids):
    """页内按边 id 排序分配调色板颜色。"""
    return {eid: EDGE_PALETTE[i % len(EDGE_PALETTE)] for i, eid in enumerate(sorted(edge_ids))}


EDGE_STYLE_SOLID = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;endArrow=block;endFill=1;strokeWidth=3;"
EDGE_STYLE_DASH = EDGE_STYLE_SOLID + "dashed=1;"

# ---------------------------------------------------------------------------
# 页面数据（绝对坐标）
# ---------------------------------------------------------------------------

def page1():
    """1-总体架构：Z1 使用方 → Z2 接入层 → App → Sandbox/Z4/Z5，横向通道走线。"""
    nodes = [
        N("z1", "Z1 · 使用方", "1", 40, 40, 360, 640, "zone"),
        N("n11", "WebUI · React SPA\n/webui", "z1", 70, 110, 300, 70),
        N("n12", "旧版静态页\n/ · /static", "z1", 70, 240, 300, 70),
        N("n13", "第三方 Web 应用", "z1", 70, 370, 300, 70),
        N("n14", "企业微信用户\nAI 机器人", "z1", 70, 500, 300, 90),
        N("z2", "Z2 · 接入层（server 进程内）", "1", 620, 40, 420, 920, "zone"),
        N("n21", "ASGI 中间件\n安全头 → API-Key 鉴权 → 限流", "z2", 650, 110, 360, 80),
        N("n22", "对话路由\n/chat/stream · /chat/ · /ws/chat\n/sessions/* · interrupt · status", "z2", 650, 270, 360, 120),
        N("n23", "渠道管理 /channels*", "z2", 650, 470, 360, 70),
        N("n24", "管理面路由\n/mcp · /skill · /images\n/admin/observe · /admin/stats", "z2", 650, 610, 360, 110),
        N("n25", "静态与健康\n/webui(dist) · /health · /me", "z2", 650, 800, 360, 80),
        N("app", "Z3 · server 容器（多进程 · 可多实例）", "1", 1240, 40, 2000, 1560, "zone"),
        N("g1", "对话域", "app", 1280, 160, 600, 380, "group"),
        N("c1", "ChatService\nFire-and-Forget · 事件驱动", "g1", 1310, 220, 260, 110),
        N("c2", "SessionManager\n恢复 · PG 回填 · fork", "g1", 1590, 220, 260, 110),
        N("c3", "SessionStatusTracker\n多端状态 · 跨端中断", "g1", 1310, 370, 260, 110),
        N("c4", "CommandService\n/new /stop /compact /help", "g1", 1590, 370, 260, 110),
        N("g5", "Agent 组装（AgentScope）", "app", 2040, 160, 600, 380, "group"),
        N("a1", "AgentFactory\nOpenAIChatModel + Formatter 选配\nToolkit = 内置工具 + MCP + Skill", "g5", 2070, 220, 540, 220),
        N("g2", "消息总线", "app", 2800, 160, 400, 380, "group"),
        N("m1", "RedisMessageBus\nPub/Sub · 分布式锁 · 会话锁\nRedis 不可用 → InMemory 降级", "g2", 2830, 220, 340, 220),
        N("g3", "渠道域（ChannelManager · server 进程内）", "app", 1280, 700, 600, 520, "group"),
        N("ch1", "ChannelManager\n启停 · 出站分发 · 配置热加载", "g3", 1310, 760, 260, 140),
        N("ch2", "wecom 适配器\nWebSocket 长连接", "g3", 1590, 760, 260, 140),
        N("ch3", "session_map\n(user, channel, chat) → session", "g3", 1310, 940, 260, 130),
        N("ch4", "feishu 等适配器 · 预留", "g3", 1590, 940, 260, 130, "reserved"),
        N("g6", "安全守卫（middleware · 洋葱链）", "app", 2040, 700, 600, 520, "group"),
        N("g6a", "① tool_guard · 工具名级", "g6", 2070, 760, 260, 80),
        N("g6b", "② command_guard · 命令内容级", "g6", 2350, 760, 260, 80),
        N("g6c", "③ docker_sandbox_proxy · 执行环境级", "g6", 2070, 880, 260, 80),
        N("g6d", "④ path_guard · 路径级", "g6", 2350, 880, 260, 80),
        N("g6e", "Memory · ReplyBudget · ContextGuard · Tracing", "g6", 2070, 1000, 540, 90),
        N("g4", "存储域", "app", 2800, 700, 400, 520, "group"),
        N("s1", "DatabaseManager\nPG(asyncpg) / MySQL(aiomysql)\nDDL · fail-fast · 503 降级", "g4", 2830, 760, 340, 110),
        N("s2", "PostgresStorage\nconversations · users · sessions", "g4", 2830, 900, 340, 100),
        N("s3", "KVStore\nredis | jsonl", "g4", 2830, 1030, 340, 85),
        N("s4", "ObjectStorage · local\n/images 代理上传", "g4", 2830, 1145, 340, 85),
        N("g9", "配置真源（JSON 文件驱动）", "app", 1280, 1360, 600, 240, "group"),
        N("cf1", "configs/{channels,mcps,skills,tools}.json\n+ agent_space/skills 磁盘目录", "g9", 1310, 1420, 540, 120),
        N("g7", "统一工作区 workspaces/", "app", 2040, 1360, 600, 240, "group"),
        N("w1", "LocalWorkspaceManager\nagent_space 共享(ro) / user_spaces 私有(rw)\nUserScopedOffloader 上下文卸载", "g7", 2070, 1420, 540, 120),
        N("g8", "支撑组件", "app", 2800, 1360, 400, 240, "group"),
        N("u1", "user_service · validators ·\ntoken_counter · multimodal ·\nobserve · stats · log · tracing", "g8", 2830, 1420, 340, 120),
        N("sb", "Z3b · Sandbox 容器（可选 · SANDBOX_BACKEND）", "1", 1240, 1720, 1200, 460, "zone"),
        N("sb1", "沙箱执行环境\ndocker exec 调度 · local 降级", "sb", 1310, 1790, 480, 130),
        N("sb2", "workspaces/\nagent_space · user_spaces/{uid}\nagent_space :ro 纵深防御", "sb", 1830, 1790, 540, 130),
        N("z4", "Z4 · 数据层", "1", 3460, 1180, 520, 640, "zone"),
        N("d1", "Redis\n热状态 · 总线 · 会话锁", "z4", 3500, 1250, 440, 100, "cyl"),
        N("d2", "PostgreSQL / MySQL\nconversations · users · sessions", "z4", 3500, 1380, 440, 100, "cyl"),
        N("d3", "对象存储 · local\n/images 文件", "z4", 3500, 1510, 440, 100, "cyl"),
        N("d4", "S3 / MinIO / OSS · 预留", "z4", 3500, 1640, 440, 90, "reserved"),
        N("z5", "Z5 · 外部依赖", "1", 3460, 40, 520, 880, "zone"),
        N("e1", "LLM API\nOpenAI 兼容 · Formatter 选配", "z5", 3500, 110, 440, 100),
        N("e2", "LLM 网关 · 预留\n计费 / 接入 / 额度", "z5", 3500, 240, 440, 90, "reserved"),
        N("e3", "OTel 栈\nJaeger · Prometheus · Loki · Grafana", "z5", 3500, 360, 440, 100),
        N("e4", "MCP 服务\nstdio / http", "z5", 3500, 490, 440, 100),
        N("e5", "Skill 库\nagent_space/skills · SKILL.md", "z5", 3500, 620, 440, 100),
        N("e6", "RAG / 向量库 · 预留\ncore/rag 空壳", "z5", 3500, 750, 440, 90, "reserved"),
        N("legend", "图例\n· 黄框=分区 · 绿框=容器 · 红框=域组 · 紫框=组件\n· 灰色虚线=预留/未落地 · 注记=浅黄虚线\n· 实线=调用或依赖 · 虚线=可选/降级/规划\n· 线走方块间通道，不穿方块、不叠线", "1", 40, 1720, 480, 220, "note"),
        N("pnote", "部署注记\n· server 容器多进程：API + Agent + 渠道 + 静态服务\n· sandbox 可选容器（SANDBOX_BACKEND=local|docker）\n· webui 仅构建期容器，dist 由 server 直接服务\n· 可多实例部署，共享 DB 与 Redis\n· 管理面（MCP/Skill/渠道）JSON 文件驱动，不落 DB 表", "1", 40, 1980, 540, 280, "note"),
    ]
    edges = [
        E("pe1", "n11", "n21"),
        E("pe2", "n12", "n21"),
        E("pe3", "n13", "n21"),
        E("pe4", "n14", "ch2", "WebSocket 长连接"),
        E("pe5", "n22", "c1", "SSE / WS / FAF"),
        E("pe6", "n23", "ch1", "管理"),
        E("pe7", "n24", "cf1", "配置 CRUD"),
        E("pe8", "n24", "s4", "图片存取"),
        E("pe9", "c1", "a1", "创建 / 执行"),
        E("pe10", "c1", "m1", "事件发布"),
        E("pe11", "ch1", "m1", "订阅 · 回发"),
        E("pe12", "a1", "g6a", "工具调用经守卫"),
        E("pe13", "g6d", "sb1", "受控执行"),
        E("pe14", "w1", "sb2", "工作区挂载"),
        E("pe15", "m1", "d1", "总线承载"),
        E("pe16", "s1", "d2", "SQL"),
        E("pe17", "s3", "d1", "KV"),
        E("pe18", "s4", "d3", "文件"),
        E("pe19", "a1", "e1", "LLM 调用"),
        E("pe20", "a1", "e4", "MCP 连接"),
        E("pe21", "a1", "e5", "Skill 加载"),
        E("pe22", "cf1", "a1", "装配", "dash"),
        E("pe23", "u1", "e3", "OTel 上报", "dash"),
    ]
    return "page-arch", "1-总体架构", 4100, 2350, nodes, edges


def page2():
    """2-对话与事件流：入口列 → 纵向主链 → 右侧落库。"""
    nodes = [
        N("f0a", "POST /chat/stream · SSE", "1", 80, 200, 520, 110),
        N("f0b", "WS /ws/chat", "1", 80, 380, 520, 110),
        N("f0c", "POST /chat/（FAF）\n+ GET /sessions/{id}/stream", "1", 80, 560, 520, 140),
        N("f0d", "渠道入站（wecom 等）", "1", 80, 770, 520, 110),
        N("f1", "1 · 斜杠命令拦截 CommandService\n/new /stop /compact /help", "1", 980, 80, 640, 130),
        N("f2", "2 · 会话状态跟踪 SessionStatusTracker\nbusy→409 · interrupt", "1", 980, 310, 640, 130),
        N("f3", "3 · 获取会话锁\nsession_lock:{user}:{session}", "1", 980, 540, 640, 130),
        N("f4", "4 · SessionManager.get_or_create\nKV 恢复 / PG 回填", "1", 980, 770, 640, 130),
        N("f5", "5 · agent.reply_stream\n守卫链拦截工具调用", "1", 980, 1000, 640, 130),
        N("f6", "6 · 发布事件 session_events\nRedisMessageBus", "1", 980, 1230, 640, 130),
        N("f7", "7 · 扇出分发\nSSE/WS 客户端 · ChannelManager 回发\nFAF 订阅端", "1", 980, 1460, 640, 150),
        N("f8", "8 · 双写落库\nPG conversations + KV AgentState/meta", "1", 980, 1700, 640, 130),
        N("fpg", "PostgreSQL", "1", 1980, 1560, 360, 110, "cyl"),
        N("fkv", "KVStore · redis | jsonl", "1", 1980, 1760, 360, 110, "cyl"),
        N("fnote", "FAF 断线补发\nmeta.last_reply 记录最后回复；\n断线重连后按 last_reply 补推", "1", 80, 1200, 560, 220, "note"),
    ]
    edges = [
        E("fe1", "f0a", "f1"),
        E("fe2", "f0b", "f1"),
        E("fe3", "f0c", "f1"),
        E("fe4", "f0d", "f1"),
        E("fe5", "f1", "f2"),
        E("fe6", "f2", "f3"),
        E("fe7", "f3", "f4"),
        E("fe8", "f4", "f5"),
        E("fe9", "f5", "f6"),
        E("fe10", "f6", "f7"),
        E("fe11", "f7", "f8"),
        E("fe12", "f8", "fpg", "写入"),
        E("fe13", "f8", "fkv", "写入"),
        E("fe14", "fpg", "f4", "回填历史", "dash"),
        E("fe15", "fkv", "f4", "KV 恢复", "dash"),
        E("fe16", "f0b", "f5", "interrupt / cancel 跨端中断", "dash"),
        E("fe17", "f0c", "f7", "断线重连 · 补发", "dash"),
    ]
    return "page-flow", "2-对话与事件流", 2500, 2100, nodes, edges


def page3():
    """3-部署拓扑：宿主机内部 + 右侧外部服务。"""
    nodes = [
        N("host", "宿主机 / Docker Compose 网络", "1", 40, 40, 1700, 1000, "zone"),
        N("wb", "webui 构建容器\n（Node 24 · 一次性）", "host", 100, 120, 480, 120),
        N("dist", "webui/dist 构建产物", "host", 100, 340, 480, 110),
        N("dnote", "编排文件\ndocker-compose.yaml · .sandbox.yml\n.channels.yaml · .channels-sandbox.yml\ncompose.otel.yml · docker/deploy_yml/", "host", 100, 560, 480, 220, "note"),
        N("appc", "server 容器\npython main.py\n挂载: 代码 / dist / workspaces / docker.sock", "host", 720, 120, 600, 220),
        N("sbc", "sandbox 容器\nsleep infinity · docker exec 调度\nagent_space :ro", "host", 720, 460, 600, 180),
        N("app2", "server 实例 2（可选 · 双实例）\n共享 DB 与 Redis", "host", 720, 760, 600, 140, "reserved"),
        N("r_redis", "Redis", "1", 1900, 120, 400, 110),
        N("r_pg", "PostgreSQL / MySQL", "1", 1900, 300, 400, 110),
        N("r_llm", "LLM API", "1", 1900, 480, 400, 110),
        N("r_otel", "OTel 栈\nJaeger / Prometheus / Loki", "1", 1900, 660, 400, 130),
    ]
    edges = [
        E("de1", "wb", "dist", "构建产物"),
        E("de2", "dist", "appc", "挂载 · 服务 /webui"),
        E("de3", "appc", "sbc", "docker exec · 受控执行"),
        E("de4", "appc", "r_redis", "总线 / 会话锁 / KV"),
        E("de5", "appc", "r_pg", "对话与会话"),
        E("de6", "appc", "r_llm", "模型调用"),
        E("de7", "appc", "r_otel", "遥测上报"),
    ]
    return "page-deploy", "3-部署拓扑", 2500, 1200, nodes, edges


def page4():
    """4-core文件级：纯清单网格，无连线。"""
    cols = [
        [
            ("core/channels/ 渠道栈", ["events.py", "base.py", "registry.py", "manager.py", "config_store.py", "catalog.py", "session_map.py", "adapters/wecom.py"], False),
            ("core/config/ 配置加载", ["schemas.py", "loader.py", "manager.py", "resolver.py"], False),
            ("core/kv/ KV 存储", ["redis_kv.py", "jsonl_kv.py"], False),
        ],
        [
            ("会话与消息", ["chat_service.py", "session.py", "session_status.py", "message_bus.py", "redis_message_bus.py"], False),
            ("存储与工作区", ["database.py", "storage.py", "storage_models.py", "workspace.py"], False),
            ("core/command/ 斜杠命令", ["router.py", "builtin.py", "service.py"], False),
            ("管理面配置", ["mcp/config_store.py", "skill/config_store.py"], False),
        ],
        [
            ("core/db/ 数据库后端", ["base.py", "factory.py", "dialect.py", "statements.py", "ddl.py", "postgres_backend.py", "mysql_backend.py"], False),
            ("支撑模块", ["user_service.py", "validators.py", "token_counter.py", "multimodal.py", "observe.py", "stats.py"], False),
            ("core/object_storage/ 对象存储", ["base.py", "local.py", "s3 预留", "oss 预留"], False),
        ],
        [
            ("core/agent/ Agent 工厂", ["factory.py"], False),
            ("其他", ["formatter/siliconflow.py", "log/", "tracing/"], False),
            ("core/rag/ 空壳 · 预留", ["third_party_store.py"], True),
        ],
    ]
    nodes = []
    x0, y0, gw, gap_x, gap_y = 60, 60, 440, 60, 50
    leaf_h, pitch = 56, 66
    for ci, col in enumerate(cols):
        y = y0
        for title, leaves, dashed in col:
            gid = "q%d_%d" % (ci, len(nodes))
            gh = 56 + len(leaves) * pitch + 16
            nodes.append(N(gid, title, "1", x0 + ci * (gw + gap_x), y, gw, gh, "group"))
            for li, leaf in enumerate(leaves):
                kind = "reserved" if ("预留" in leaf or dashed) else "leaf"
                # 注意：全脚本统一用画布绝对坐标，相对父框的换算在 build_drawio 里做
                nodes.append(N(gid + "f%d" % li, leaf, gid,
                               x0 + ci * (gw + gap_x) + 20, y + 56 + li * pitch,
                               gw - 40, leaf_h, kind))
            y += gh + gap_y
    return "page-core", "4-core文件级", 2200, 1650, nodes, []


PAGES = [page1, page2, page3, page4]


# ---------------------------------------------------------------------------
# 布线：A* 通道走线
# ---------------------------------------------------------------------------

def cell_of(x, y):
    return int(x // GRID), int(y // GRID)


def cell_center(ix, iy):
    return ix * GRID + GRID // 2, iy * GRID + GRID // 2


def snap_centers(nodes):
    """把每个方块中心吸附到网格格点（中心 ≡ 5 mod 10），保证首尾线段正交。"""
    for n in nodes:
        cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
        tx = round((cx - GRID // 2) / GRID) * GRID + GRID // 2
        ty = round((cy - GRID // 2) / GRID) * GRID + GRID // 2
        n["x"] += tx - cx
        n["y"] += ty - cy


def rect_covers(n, x, y):
    return n["x"] <= x < n["x"] + n["w"] and n["y"] <= y < n["y"] + n["h"]


def point_in_rect(p, r, pad=0.5):
    return (r["x"] + pad <= p[0] <= r["x"] + r["w"] - pad
            and r["y"] + pad <= p[1] <= r["y"] + r["h"] - pad)


def seg_cross_point(p, q, r):
    """p 在矩形内、q 在外的正交线段与边框的交点。"""
    if abs(p[1] - q[1]) < 1e-9:
        x = r["x"] + r["w"] if q[0] > p[0] else r["x"]
        return (x, p[1])
    y = r["y"] + r["h"] if q[1] > p[1] else r["y"]
    return (p[0], y)


def trim_edge_ends(pts, src, dst):
    """把路径首尾裁到方块边框，返回 (显示路径, 出线锚点, 入线锚点)。

    线不再画进方块内部，进出线在边框处对接（exitX/entryX 锚点）。
    """
    si = 0
    while si < len(pts) - 1 and point_in_rect(pts[si + 1], src):
        si += 1
    if si >= len(pts) - 1:
        return None, None, None
    exit_pt = seg_cross_point(pts[si], pts[si + 1], src)
    j = len(pts) - 1
    while j > 0 and point_in_rect(pts[j - 1], dst):
        j -= 1
    if j <= si:
        return None, None, None
    entry_pt = seg_cross_point(pts[j], pts[j - 1], dst)
    display = [exit_pt] + pts[si + 1:j] + [entry_pt]
    # 去掉相邻共线点
    simp = [display[0]]
    for p in display[1:]:
        simp.append(p)
        while len(simp) >= 3:
            a, b, c = simp[-3], simp[-2], simp[-1]
            if (abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6) or (
                    abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6):
                simp.pop(-2)
            else:
                break
    return simp, exit_pt, entry_pt


def anchor_frac(pt, n):
    return ((pt[0] - n["x"]) / n["w"], (pt[1] - n["y"]) / n["h"])


def build_context(nodes):
    by_id = {n["id"]: n for n in nodes}
    parent_of = {n["id"]: n["parent"] for n in nodes}

    def ancestors(nid):
        out = set()
        cur = parent_of.get(nid)
        while cur and cur != "1":
            out.add(cur)
            cur = parent_of.get(cur)
        return out

    return by_id, ancestors


def route_edge(edge, nodes, by_id, ancestors, used, W, H):
    src, dst = by_id[edge["src"]], by_id[edge["dst"]]
    exempt = {src["id"], dst["id"]} | ancestors(src["id"]) | ancestors(dst["id"])
    sx, sy = src["x"] + src["w"] / 2, src["y"] + src["h"] / 2
    dx, dy = dst["x"] + dst["w"] / 2, dst["y"] + dst["h"] / 2
    s_cell, d_cell = cell_of(sx, sy), cell_of(dx, dy)
    end_rects = (src, dst)

    def in_end_rect(ix, iy):
        x, y = cell_center(ix, iy)
        return any(rect_covers(r, x, y) for r in end_rects)

    blocked = set()
    inflate = GRID  # 障碍膨胀 10px，线与方块保持可见间隙
    for n in nodes:
        if n["id"] in exempt:
            continue
        x1, y1 = cell_of(n["x"] - inflate, n["y"] - inflate)
        x2, y2 = cell_of(n["x"] + n["w"] + inflate, n["y"] + n["h"] + inflate)
        for ix in range(x1, x2 + 1):
            for iy in range(y1, y2 + 1):
                cx, cy = cell_center(ix, iy)
                if (n["x"] - inflate <= cx < n["x"] + n["w"] + inflate
                        and n["y"] - inflate <= cy < n["y"] + n["h"] + inflate):
                    blocked.add((ix, iy))

    dirs = [(1, 0, "h"), (0, 1, "v"), (-1, 0, "h"), (0, -1, "v")]
    gx, gy = d_cell
    start = (s_cell[0], s_cell[1], -1)
    seq = 0
    openq = [((abs(s_cell[0] - gx) + abs(s_cell[1] - gy)) * GRID, seq, 0.0, start)]
    best = {start: 0.0}
    parent = {}
    final = None
    while openq:
        _, _, cost, state = heapq.heappop(openq)
        if cost > best.get(state, 1e18) + 1e-6:
            continue
        ix, iy, d = state
        if (ix, iy) == (gx, gy):
            final = state
            break
        for di, (mx, my, axis) in enumerate(dirs):
            if d != -1 and di == (d + 2) % 4:
                continue  # 不允许原地掉头
            nx, ny = ix + mx, iy + my
            if not (0 <= nx * GRID < W and 0 <= ny * GRID < H):
                continue
            if (nx, ny) in blocked:
                continue
            step = GRID
            if d != -1 and dirs[d][2] != axis:
                step += 25  # 转弯代价
            if not in_end_rect(nx, ny):
                lanes = used.get((nx, ny))
                if lanes:
                    if axis in lanes:
                        continue  # 禁止同走廊叠线
                    step += 80  # 垂直交叉代价
            nstate = (nx, ny, di)
            ncost = cost + step
            if ncost < best.get(nstate, 1e18) - 1e-9:
                best[nstate] = ncost
                parent[nstate] = state
                h = (abs(nx - gx) + abs(ny - gy)) * GRID
                seq += 1
                heapq.heappush(openq, (ncost + h, seq, ncost, nstate))
    if final is None:
        return None
    # 回溯
    path_states = [final]
    while path_states[-1] in parent:
        path_states.append(parent[path_states[-1]])
    path_states.reverse()
    pts = [(sx, sy)] + [cell_center(ix, iy) for ix, iy, _ in path_states[1:-1]] + [(dx, dy)]
    # 去掉共线中间点
    simp = [pts[0]]
    for p in pts[1:]:
        simp.append(p)
        while len(simp) >= 3:
            a, b, c = simp[-3], simp[-2], simp[-1]
            if abs(a[0] - b[0]) < 1e-6 and abs(b[0] - c[0]) < 1e-6:
                simp.pop(-2)
            elif abs(a[1] - b[1]) < 1e-6 and abs(b[1] - c[1]) < 1e-6:
                simp.pop(-2)
            else:
                break
    # 登记走廊占用（端点方块内部除外）
    for st1, st2 in zip(path_states, path_states[1:]):
        (ix, iy, _d) = st2
        mx = st2[0] - st1[0]
        axis = "h" if mx else "v"
        if not in_end_rect(ix, iy):
            used[(ix, iy)].add(axis)
    display, exit_pt, entry_pt = trim_edge_ends(simp, src, dst)
    if display is None:
        return None
    return display, anchor_frac(exit_pt, src), anchor_frac(entry_pt, dst)


# ---------------------------------------------------------------------------
# 几何校验
# ---------------------------------------------------------------------------

def seg_rect_hit(x1, y1, x2, y2, r, inset=0.5):
    """线段是否进入矩形内部（内缩 inset 后仍相交）。"""
    rx, ry, rw, rh = r["x"] + inset, r["y"] + inset, r["w"] - 2 * inset, r["h"] - 2 * inset
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - rx), (dx, rx + rw - x1), (-dy, y1 - ry), (dy, ry + rh - y1)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t1 > t0 + 1e-9


def hidden_in_node(x1, y1, x2, y2, nodes):
    """叠压段是否完全藏在某个方块内部（视觉上不可见，不计违规）。"""
    for n in nodes:
        if (n["x"] - 1 <= x1 <= n["x"] + n["w"] + 1 and n["y"] - 1 <= y1 <= n["y"] + n["h"] + 1
                and n["x"] - 1 <= x2 <= n["x"] + n["w"] + 1 and n["y"] - 1 <= y2 <= n["y"] + n["h"] + 1):
            return True
    return False


def verify(page, nodes, edge_pts):
    by_id, ancestors = build_context(nodes)
    errors, crossings = [], 0

    # 1) 同级方块不得重叠
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            if a["parent"] != b["parent"]:
                continue
            ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ox > 1 and oy > 1:
                errors.append("节点重叠: %s x %s" % (a["id"], b["id"]))

    # 1b) 子节点必须完全落在父容器内（防止绝对/相对坐标混用漏检）
    for n in nodes:
        if n["parent"] == "1":
            continue
        p = by_id.get(n["parent"])
        if p is None:
            errors.append("父容器缺失: %s -> %s" % (n["id"], n["parent"]))
            continue
        if (n["x"] < p["x"] - 6 or n["y"] < p["y"] - 6
                or n["x"] + n["w"] > p["x"] + p["w"] + 6
                or n["y"] + n["h"] > p["y"] + p["h"] + 6):
            errors.append("子节点越界: %s 不在父容器 %s 内" % (n["id"], n["parent"]))

    # 2) 线段不得穿过无关方块
    for eid, info in edge_pts.items():
        pts = info["pts"]
        ed = edge_by_id[eid]
        exempt = {ed["src"], ed["dst"]} | ancestors(ed["src"]) | ancestors(ed["dst"])
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(x1 - x2) > 1e-6 and abs(y1 - y2) > 1e-6:
                errors.append("非正交线段: %s (%g,%g)-(%g,%g)" % (eid, x1, y1, x2, y2))
            for n in nodes:
                if n["id"] in exempt:
                    continue
                inflated = {"x": n["x"] - 5, "y": n["y"] - 5, "w": n["w"] + 10, "h": n["h"] + 10}
                if seg_rect_hit(x1, y1, x2, y2, inflated):
                    errors.append("线穿方块: %s 穿过 %s" % (eid, n["id"]))

    # 3) 线段走廊叠压 / 交叉统计
    segs = []
    for eid, info in edge_pts.items():
        for (x1, y1), (x2, y2) in zip(info["pts"], info["pts"][1:]):
            segs.append((eid, x1, y1, x2, y2))
    for i, (e1, ax1, ay1, ax2, ay2) in enumerate(segs):
        for e2, bx1, by1, bx2, by2 in segs[i + 1:]:
            if e1 == e2:
                continue
            a_vert = abs(ax1 - ax2) < 1e-6
            b_vert = abs(bx1 - bx2) < 1e-6
            if a_vert == b_vert:
                if a_vert:
                    if abs(ax1 - bx1) < 3:
                        lo = max(min(ay1, ay2), min(by1, by2))
                        hi = min(max(ay1, ay2), max(by1, by2))
                        if hi - lo > 3 and not hidden_in_node(ax1, lo, ax1, hi, nodes):
                            errors.append("线叠压: %s 与 %s 竖直同轨" % (e1, e2))
                else:
                    if abs(ay1 - by1) < 3:
                        lo = max(min(ax1, ax2), min(bx1, bx2))
                        hi = min(max(ax1, ax2), max(bx1, bx2))
                        if hi - lo > 3 and not hidden_in_node(lo, ay1, hi, ay1, nodes):
                            errors.append("线叠压: %s 与 %s 水平同轨" % (e1, e2))
            else:
                # 垂直交叉计数
                if a_vert:
                    if min(ay1, ay2) <= by1 <= max(ay1, ay2) and min(bx1, bx2) <= ax1 <= max(bx1, bx2):
                        crossings += 1
                else:
                    if min(ax1, ax2) <= bx1 <= max(ax1, ax2) and min(by1, by2) <= ay1 <= max(by1, by2):
                        crossings += 1
    return errors, crossings


# ---------------------------------------------------------------------------
# 边标签防重叠：优先放在最长段中点，碰撞时沿走线/垂直方向贪心推开，
# 结果记入 edge_pts[...]["loff"] = (along, side, off_dx, off_dy)，PNG 与 drawio 共用
# ---------------------------------------------------------------------------

def label_anchor(pts, along=0.0, side=0.0):
    """最长段上按 along（-0.5..0.5）取锚点，side 为垂直像素偏移。"""
    best = max(zip(pts, pts[1:]),
               key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
    (x1, y1), (x2, y2) = best
    f = 0.5 + along
    mx = x1 + (x2 - x1) * f
    my = y1 + (y2 - y1) * f
    if abs(y1 - y2) < 1e-6:
        return mx, my, 0.0, float(side)   # 水平段 → 垂直偏移
    return mx, my, float(side), 0.0       # 竖直段 → 水平偏移


def path_frac(pts, px, py):
    """锚点沿整条折线的长度分数（0=起点 1=终点），换算 drawio 标签 x 用。"""
    total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    if total < 1e-9:
        return 0.0
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg < 1e-9:
            continue
        t = ((px - a[0]) * (b[0] - a[0]) + (py - a[1]) * (b[1] - a[1])) / (seg * seg)
        t = min(1.0, max(0.0, t))
        qx, qy = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
        if math.hypot(qx - px, qy - py) < 1.0:
            return (acc + t * seg) / total
        acc += seg
    return 0.5


def resolve_labels(edge_pts, edge_meta):
    """贪心放置边标签：候选位次由近及远，避开已放标签框和所有其他线段。"""
    def text_w(s):
        return sum(16 if ord(c) > 127 else 8 for c in s)

    all_segs = []
    for eid0, info0 in edge_pts.items():
        for (x1, y1), (x2, y2) in zip(info0["pts"], info0["pts"][1:]):
            all_segs.append((eid0, x1, y1, x2, y2))

    placed = []
    for eid in sorted(edge_pts):
        meta = edge_meta.get(eid)
        if not meta or not meta.get("label"):
            continue
        pts = edge_pts[eid]["pts"]
        w = text_w(meta["label"]) + 12
        # 候选优先级：贴自己的线沿线滑动（语义最近）→ 小幅离线 → 大幅离线
        cands = [(0.0, 0.0)]
        for a in (0.18, -0.18, 0.34, -0.34):
            cands.append((a, 0.0))
        for s in (28.0, -28.0):
            for a in (0.0, 0.18, -0.18, 0.34, -0.34):
                cands.append((a, s))
        for s in (56.0, -56.0, 88.0, -88.0, 120.0, -120.0):
            for a in (0.0, 0.18, -0.18, 0.34, -0.34):
                cands.append((a, s))

        def box_of(along, side):
            mx, my, odx, ody = label_anchor(pts, along, side)
            return (mx + odx - 4, my + ody - 12, mx + odx + w + 4, my + ody + 12), \
                   (along, side, odx, ody)

        chosen = None
        for along, side in cands:
            box, loff = box_of(along, side)
            if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                   for b in placed):
                continue
            r = {"x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1]}
            if any(se != eid and seg_rect_hit(x1, y1, x2, y2, r)
                   for se, x1, y1, x2, y2 in all_segs):
                continue
            chosen = (loff, box)
            break
        if chosen is None:
            loff, box = box_of(*cands[-1])
            chosen = (loff, box)
        placed.append(chosen[1])
        edge_pts[eid]["loff"] = chosen[0]


# ---------------------------------------------------------------------------
# 渲染 PNG 预览
# ---------------------------------------------------------------------------

def render_png(path, W, H, nodes, edge_pts, edge_meta, scale=0.6):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img, "RGBA")

    def font(sz, bold=False):
        f = "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"
        try:
            return ImageFont.truetype(f, sz)
        except OSError:
            return ImageFont.load_default()

    f_zone, f_group, f_leaf, f_note = font(28, True), font(23, True), font(19), font(18)
    f_edge = font(15)

    fills = {
        "zone": ("#FFF2CC", "#D6B656", f_zone, "#5B4A00"),
        "group": ("#F8CECC", "#B85450", f_group, "#6B1F1C"),
        "leaf": ("#ECECFF", "#9370DB", f_leaf, "#2B2146"),
        "cyl": ("#ECECFF", "#9370DB", f_leaf, "#2B2146"),
        "reserved": ("#F5F5F5", "#999999", f_leaf, "#777777"),
        "note": ("#FFFDE7", "#D6B656", f_note, "#5B4A00"),
    }
    order = {"zone": 0, "group": 1, "note": 2, "cyl": 3, "reserved": 3, "leaf": 3}
    for n in sorted(nodes, key=lambda n: (order[n["kind"]], n["x"])):
        fill, stroke, fnt, tcol = fills[n["kind"]]
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        if n["kind"] == "cyl":
            dr.ellipse([x, y - 8, x + w, y + 22], fill=fill, outline=stroke, width=3)
            dr.rectangle([x, y + 7, x + w, y + h - 12], fill=fill, outline=None)
            dr.line([x, y + 7, x, y + h - 12], fill=stroke, width=3)
            dr.line([x + w, y + 7, x + w, y + h - 12], fill=stroke, width=3)
            dr.ellipse([x, y + h - 32, x + w, y + h], fill=fill, outline=stroke, width=3)
            dr.rectangle([x, y + 7, x + w, y + h - 12], outline=None)
            dr.arc([x, y + h - 32, x + w, y + h], 0, 180, fill=stroke, width=3)
        else:
            dashed = n["kind"] == "reserved" or n["kind"] == "note"
            if dashed:
                # 虚线边框
                per = 2 * (w + h)
                seg, gap = 10, 6
                t = 0
                pts_box = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
                for (x1, y1), (x2, y2) in zip(pts_box, pts_box[1:]):
                    length = math.hypot(x2 - x1, y2 - y1)
                    pos = 0
                    while pos < length:
                        a = pos / length
                        b = min(pos + seg, length) / length
                        dr.line([x1 + (x2 - x1) * a, y1 + (y2 - y1) * a,
                                 x1 + (x2 - x1) * b, y1 + (y2 - y1) * b], fill=stroke, width=3)
                        pos += seg + gap
                dr.rectangle([x, y, x + w, y + h], fill=fill, outline=None)
            else:
                dr.rectangle([x, y, x + w, y + h], fill=fill, outline=stroke, width=3)
        tx, ty = x + 12, y + 8
        if n["kind"] in ("zone", "group"):
            dr.text((tx, ty), n["label"].split("\n")[0], font=fnt, fill=tcol)
            dr.text((tx, ty + 34), "\n".join(n["label"].split("\n")[1:]), font=f_leaf, fill=tcol)
        else:
            dr.multiline_text((tx, ty), n["label"], font=fnt, fill=tcol, spacing=6)

    col_of = edge_colors(edge_pts.keys())
    for eid, info in sorted(edge_pts.items()):
        pts = info["pts"]
        col = col_of[eid]
        meta = edge_meta[eid]
        dashed = meta["style"] == "dash"
        if dashed:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                length = math.hypot(x2 - x1, y2 - y1)
                pos = 0.0
                while pos < length:
                    a = pos / length
                    b = min(pos + 12, length) / length
                    dr.line([x1 + (x2 - x1) * a, y1 + (y2 - y1) * a,
                             x1 + (x2 - x1) * b, y1 + (y2 - y1) * b], fill=col, width=5)
                    pos += 12 + 8
        else:
            dr.line(pts, fill=col, width=5, joint="curve")
        # 箭头
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        size = 16
        p1 = (x2, y2)
        p2 = (x2 - size * math.cos(ang - 0.45), y2 - size * math.sin(ang - 0.45))
        p3 = (x2 - size * math.cos(ang + 0.45), y2 - size * math.sin(ang + 0.45))
        dr.polygon([p1, p2, p3], fill=col)
        if meta["label"]:
            # 标签位置来自 resolve_labels 的防重叠结果
            along, side, dx, dy = info.get("loff", (0.0, 0.0, 0.0, 0.0))
            mx, my, _, _ = label_anchor(pts, along, side)
            lx, ly = mx + dx, my + dy
            tw = dr.textlength(meta["label"], font=f_edge)
            dr.rectangle([lx - 4, ly - 12, lx + tw + 8, ly + 12], fill="#FFFFFFEE")
            dr.text((lx + 4, ly - 11), meta["label"], font=f_edge, fill="#333333")

    img = img.resize((int(W * scale), int(H * scale)), Image.LANCZOS)
    img.save(path)
    return path


# ---------------------------------------------------------------------------
# 生成 .drawio
# ---------------------------------------------------------------------------

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def label_xml(label):
    return esc(label).replace("\n", "&lt;br&gt;")


def build_drawio(page_results):
    out = ['<mxfile host="Claude-Code" agent="drawio-layout" version="24.7.7">']
    for pid, pname, W, H, nodes, edges, edge_pts in page_results:
        out.append('  <diagram id="%s" name="%s">' % (pid, esc(pname)))
        out.append('    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d" math="0" shadow="0">' % (W, H))
        out.append("      <root>")
        out.append('        <mxCell id="0" />')
        out.append('        <mxCell id="1" parent="0" />')
        abs_pos = {}
        for n in nodes:
            abs_pos[n["id"]] = (n["x"], n["y"])
        for n in nodes:
            px, py = abs_pos.get(n["parent"], (0, 0)) if n["parent"] != "1" else (0, 0)
            out.append('        <mxCell id="%s" value="%s" style="%s" vertex="1" parent="%s">' % (
                n["id"], label_xml(n["label"]), KIND_STYLE[n["kind"]], n["parent"]))
            out.append('          <mxGeometry x="%g" y="%g" width="%g" height="%g" as="geometry" />' % (
                n["x"] - px, n["y"] - py, n["w"], n["h"]))
            out.append("        </mxCell>")
        col_of = edge_colors([x["id"] for x in edges])
        for e in edges:
            style = (EDGE_STYLE_DASH if e["style"] == "dash" else EDGE_STYLE_SOLID) + "strokeColor=%s;" % col_of[e["id"]]
            info = edge_pts[e["id"]]
            (ex, ey), (nx, ny) = info["exit"], info["entry"]
            style += "exitX=%g;exitY=%g;entryX=%g;entryY=%g;" % (ex, ey, nx, ny)
            val = ' value="%s"' % esc(e["label"]) if e["label"] else ""
            out.append('        <mxCell id="%s"%s style="%s" edge="1" parent="1" source="%s" target="%s">' % (
                e["id"], val, style, e["src"], e["dst"]))
            pts = info["pts"]
            gx_attr = ""
            off_xml = ""
            if e["label"]:
                along, side, odx, ody = info.get("loff", (0.0, 0.0, 0.0, 0.0))
                mx, my, _, _ = label_anchor(pts, along, side)
                frac = path_frac(pts, mx, my) - 0.5   # drawio 标签 x: -0.5 源端 / 0.5 目标端
                gx_attr = ' x="%g" y="0"' % frac
                off_xml = '            <mxPoint as="offset" x="%g" y="%g" />\n' % (odx, ody)
            out.append('          <mxGeometry%s relative="1" as="geometry">' % gx_attr)
            mid = pts[1:-1]
            if mid:
                out.append('            <Array as="points">')
                for x, y in mid:
                    out.append('              <mxPoint x="%g" y="%g" />' % (x, y))
                out.append("            </Array>")
            if off_xml:
                out.append("            " + off_xml.strip("\n"))
            out.append("          </mxGeometry>")
            out.append("        </mxCell>")
        out.append("      </root>")
        out.append("    </mxGraphModel>")
        out.append("  </diagram>")
    out.append("</mxfile>")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

edge_by_id = {}


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(tempfile.gettempdir(), "drawio_layout_check")
    os.makedirs(out_dir, exist_ok=True)
    page_results = []
    all_errors = 0
    for fn in PAGES:
        pid, pname, W, H, nodes, edges = fn()
        snap_centers(nodes)
        by_id, ancestors = build_context(nodes)
        used = defaultdict(set)
        edge_pts = {}
        print("== %s (%d nodes, %d edges) ==" % (pname, len(nodes), len(edges)))
        for e in edges:
            edge_by_id[e["id"]] = e
            routed = route_edge(e, nodes, by_id, ancestors, used, W, H)
            if routed is None:
                print("  [FAIL] 布线失败: %s (%s -> %s)" % (e["id"], e["src"], e["dst"]))
                all_errors += 1
                continue
            display, exit_frac, entry_frac = routed
            edge_pts[e["id"]] = {"pts": display, "exit": exit_frac, "entry": entry_frac}
        resolve_labels(edge_pts, {e["id"]: e for e in edges})
        errs, crossings = verify(pid, nodes, edge_pts)
        for msg in errs:
            print("  [FAIL]", msg)
        all_errors += len(errs)
        print("  校验: %d 处问题, 垂直交叉 %d 处" % (len(errs), crossings))
        png = render_png(os.path.join(out_dir, pid + ".png"), W, H, nodes, edge_pts,
                         {e["id"]: e for e in edges})
        print("  预览:", png)
        page_results.append((pid, pname, W, H, nodes, edges, edge_pts))

    xml = build_drawio(page_results)
    target = os.path.join(repo, "docs", "Agent开发架构-v2.drawio")
    with open(target, "w", encoding="utf-8") as f:
        f.write(xml)
    print("写入:", target)
    if all_errors:
        print("存在问题 %d 处" % all_errors)
        sys.exit(1)
    print("全部校验通过")


if __name__ == "__main__":
    main()
