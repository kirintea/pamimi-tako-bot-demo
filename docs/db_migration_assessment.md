# PostgreSQL → MySQL 迁移可行性评估

> 状态：仅评估，未改动任何代码
> 评估日期：2026-09-20
> 范围说明：**本次仅评估存储引擎替换的技术可行性，不涉及数据迁移**——开测阶段数据可丢弃，新部署直接从空库开始，因此无需考虑 PG→MySQL 存量数据转换与迁移脚本。

## 一、结论先行

**技术上完全可行。** 项目没有使用任何"MySQL 无法替代"的 PostgreSQL 专有特性（无 JSONB 操作符 `->>`/`@>`、`ARRAY` 类型、advisory lock、触发器、CTE、窗口函数、存储过程）。

**但非零成本。** 关键在于：项目是**自己用 `asyncpg` + 原生 SQL 手写了一整套 PG 存储层**（`core/database.py` + `core/storage.py`），并非基于 SQLAlchemy 这类方言无关的 ORM。因此切换需要**中等偏上的改造量**，主要集中在驱动替换、DDL 重写和 SQL 语法改写，改动集中、可控。

## 二、现状盘点（关键事实）

| 层 | 文件 | PG 耦合方式 |
| --- | --- | --- |
| 连接池 | `core/database.py` | `asyncpg.create_pool`，全量 `$N` 占位符 |
| 自动建表 | `core/database.py:30-262` | 手写 DDL：`BIGSERIAL` / `JSONB` / `TIMESTAMPTZ` + 部分索引 + `ADD COLUMN IF NOT EXISTS` |
| 幂等逻辑 | `core/database.py:318-335` | 依赖 PG `sqlstate` 错误码 `42P07`/`42710`/`42P16` |
| CRUD | `core/storage.py` | `ON CONFLICT ... DO UPDATE` + `RETURNING id` + `"DELETE 1" in result` 判断 |
| 健康检查 | `health_check/check_postgres.py`、`api/chat.py:421-431` | 硬编码 `asyncpg` + `information_schema.table_schema='public'` |
| 配置 | `core/config/schemas.py:202` | `DatabaseConfig` 仅含 `url` + `pool_size`，**完全通用，未绑死 PG** |

## 三、必须改造的点（按工作量排序）

### 1. 驱动层（必改，集中）

- `asyncpg` → `aiomysql` 或 `asyncmy`（建议 `aiomysql`，生态成熟）。
- 占位符 `$1, $2, ...` → `%s`（几乎每条 SQL 都要改）。
- 必须使用**字典游标**（dict cursor），否则 `storage.py` 中 `row["col"]` 字典式访问会失效。

### 2. DDL / 自动建表（必改，工作量最大）

- `BIGSERIAL` → `BIGINT AUTO_INCREMENT`
- `JSONB` → `JSON`
- `TIMESTAMPTZ` → `TIMESTAMP`
  - ⚠️ **MySQL 单表仅允许一列 `DEFAULT CURRENT_TIMESTAMP`**。`sessions` 表的 `created_at` 与 `updated_at` 都想要默认时间，需用 `ON UPDATE CURRENT_TIMESTAMP`（仅给 `updated_at`）或在应用层赋值。
- 🔴 **部分索引不支持**：`CREATE INDEX ... WHERE status='active'`（`database.py:57/69/75/109/113/118/247`）在 MySQL 下全部报错，需去掉 `WHERE` 改全量索引，或改用生成列（generated column）+ 条件索引方案。
- 🔴 **`ADD COLUMN IF NOT EXISTS` 不支持**：MySQL 无此语法（`database.py:47/99/104/238/242`），需改为"先查 `information_schema` 再 ALTER"，或整体改用 Alembic 做幂等迁移。
- **幂等错误码**：PG 的 `42P07` 等 → MySQL 的 `1050`(表已存在)/`1060`(列已存在)/`1061`(索引已存在)/`1062`(唯一冲突)，`_run_ddl` 需重写。

### 3. CRUD SQL（必改，约 11 处）

- 6 处 upsert：
  - `ON CONFLICT (col) DO UPDATE SET ... RETURNING id` → `INSERT ... ON DUPLICATE KEY UPDATE`；
  - 去掉 `RETURNING`；VARCHAR 主键表（如 `sessions`，id 调用方已知）可直接返回；`conversations` 自增表用 `LAST_INSERT_ID()` 取回 id。
- 5 处 delete：`return "DELETE 1" in result`（`storage.py:116/323/427/529/741`）→ 改判 `cursor.rowcount > 0`。

### 4. 健康检查（必改，简单）

- `health_check/check_postgres.py` 与 `api/chat.py` 健康检查的 `asyncpg` + `table_schema='public'` 查询 → 改为 MySQL 驱动 + `table_schema = DATABASE()`。

### 5. 配置（几乎不改）

- `DATABASE_URL` 由 `postgresql://...` 改为 `mysql+aiomysql://...`；
- `configs/*.yaml` 的默认值同步修改（`dev.yaml` / `prod.yaml` 的 `database.url` 默认串）。

## 四、降低风险的利好点（重要）

- `storage.py` 的 `_row_to_agent` 已做 `json.loads(row["data"]) if isinstance(row["data"], str)` 兼容，**JSON 反序列化无需改**。
- 全程仅整列存取 JSON，**未使用 JSONB 操作符**，`JSON` 类型即可等价替代。
- 会话锁、运行锁均走 **Redis**（`core/redis_message_bus.py`、`chat_service.py`），DB 层不依赖 PG 事务/锁特性，并发风险低。
- vendored 的 `example/agentscope-main` 内 `AsyncSQLAlchemyStorage` 已支持 mysql/sqlite/postgres，团队有现成方言经验可借鉴。

## 五、推荐方案（供决策）

| 方案 | 思路 | 工作量 | 风险 | 长期收益 |
| --- | --- | --- | --- | --- |
| **A（轻量，推荐先做）** | 就地改造：换 `aiomysql` + 重写 DDL/SQL/占位符 + 改健康检查 | 中 | 中低 | 低，仍绑定原生 SQL |
| **B（彻底）** | 把 `core/storage.py` 重写为基于 SQLAlchemy async（复用 agentscope 现成 `AsyncSQLAlchemyStorage` 模式） | 高 | 中 | 高，方言无关，可任意切库 |

**说明**：方案 A 改动集中在 5~7 个文件，可控性强，适合"开测阶段快速切换"的目标；方案 B 长期更优但工作量更大，可在后续稳定后演进。

## 六、本次评估明确排除的范围

- ❌ PG→MySQL 存量数据迁移脚本（开测数据可丢弃，新部署从空库起步）
- ❌ 在线热迁移 / 双写过渡方案
- ❌ 具体改造的代码实现（仅评估）

## 七、下一步可选动作

1. 按 **方案 A** 输出可落地的改造计划（Plan 模式：每处改动 + 受影响行号）；
2. 按 **方案 B** 输出基于 SQLAlchemy 的重写方案对比；
3. 直接进入改造实施（方案 A）。
