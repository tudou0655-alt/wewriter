# Current handoff — T-002 版本化 SQLite 迁移

Updated: 2026-10-16
From: Codex
To: human
Task: T-002
Status: `done`

## Final disposition

- Codex 已完成两轮只读审查；较新版本数据库被旧程序修改的问题已修复，最终结论为通过。
- Codex 复跑迁移专项测试 8 项和全量测试 129 项，全部通过。
- 人工已于 2026-10-16 确认验收完成，并授权标记 `done`、创建本地提交及推送 GitHub。

## How T-002 was chosen

Claude 按上一份交接做只读发现后，在项目线程推荐了“版本化 SQLite 迁移”，范围是 `wewriter/db.py`、`tests/test_db_migrations.py` 加两份协作文档；人工于 2026-10-16 回复“迁移”批准。本任务没有单独的 REQUIREMENTS/PLAN 更新（不在获准文件内），范围和验收标准记录在 `TASKS.md` 的 T-002 条目。

## Implementation result

- Branch: `codex/t001-github`，基于 `691ee09`；未提交、未推送、未切换或创建分支。
- `wewriter/db.py`：
  - 从 `SCHEMA` 中删除 `PRAGMA user_version=1`（以前每次启动都会把版本写回 1）。
  - 新增 `MIGRATIONS = {2: _migrate_v2}` 和 `SCHEMA_VERSION = max(MIGRATIONS)`。`_migrate_v2` 把原 `cleanup_schema` 的补列逻辑（`articles`/`drafts.cleared`、`jobs.error_code`）改成幂等的 v1→v2 步骤，经 `_add_column` 辅助函数执行。
  - `Database.migrate_schema(db)` 替换 `cleanup_schema`：`check_version` → 执行 `SCHEMA` 建缺失表 → 按序执行高于当前版本的迁移 → 写入 `SCHEMA_VERSION`。
  - 第 1 轮审查修改（人工转达，2026-10-16）：新增 `Database.check_version(db)`。`__init__` 在 `PRAGMA journal_mode=WAL` 和 `executescript(SCHEMA)` 之前调用它；`user_version > SCHEMA_VERSION` 时抛出 `ValueError`（“数据库来自更新版本的软件……请升级软件后再打开”），不对库做任何修改。之前“不降级、继续打开”的行为已删除。
  - `restore()`：接受 `1 <= user_version <= SCHEMA_VERSION` 的备份，恢复后通过 `migrate_schema` 自动升级；高于当前版本时报“备份来自更新版本的软件，请先升级后再恢复”；版本 0 仍报“备份版本不匹配”。
- `tests/test_db_migrations.py`：8 项测试，覆盖新库、缺列的 v1 库升级并保留账号/文章/任务、部分升级的 v1 库、重复启动幂等、拒绝较新版本的库且版本号/journal_mode/表结构/数据快照均不变、恢复 v1 备份后升级（且不改动备份文件）、拒绝较新版本备份且当前数据不受影响、拒绝无版本备份。临时目录与 `test_core.py` 一致，使用 `.local-data/tests`。测试数据为虚构值，不涉及真实用户库。

## Verification evidence (Claude, 2026-10-16)

| Check | Command | Observed result |
|---|---|---|
| 新增测试（审查修改后复跑） | `.\.venv\Scripts\python.exe -m unittest tests.test_db_migrations -v` | `Ran 8 tests in 0.497s`，`OK`（首轮：`Ran 8 tests in 0.437s`，`OK`） |
| 全量测试（审查修改后复跑） | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 129 tests in 9.948s`，`OK`（121 基线 + 8 新增；首轮 10.685s，OK） |
| 空白检查 | `git diff --check` | 退出码 0；仅有 `HANDOFF.md`、`TASKS.md` 的 `LF will be replaced by CRLF` 提示 |
| 工作区 | `git status --short` | ` M docs/collab/HANDOFF.md`、` M docs/collab/TASKS.md`、` M wewriter/db.py`、`?? tests/test_db_migrations.py` |

## Notes for review / residual risks

- 兼容性取舍：新版本写入的数据库/备份为 `user_version=2`。旧版本（v0.10.3 及以前）恢复这类备份会报“备份版本不匹配”；旧版本直接打开新库会把版本写回 1，新版本再次打开时幂等迁移会重新升到 2，不会丢数据。
- 迁移非原子：`executescript` 会先提交，Python sqlite3 也不会为 `ALTER TABLE` 隐式开启事务。依靠步骤幂等保证中途失败后可重跑，没有在迁移前自动备份。
- 遇到较新版本的库时，`Database()` 抛出 `ValueError`。`main.py:78` 没有捕获它，所以启动会中止，异常只经 `logs` 的 excepthook 写入日志，用户界面不会弹窗提示。如需弹窗要改 `main.py`，不在本任务获准文件内。
- 本次未改动 `REQUIREMENTS.md`、`PLAN.md`；若需正式需求/计划记录，请人工或 Codex 补充。
- 工作区开始时 `HANDOFF.md` 已有 Codex 未提交的修改，已保留在下方。

## Exact next action

创建 T-002 本地提交并以普通快进推送到 GitHub `main`；不得强制推送。

---

# Previous handoff — Codex: T-001 completed / next-task discovery

Updated: 2026-10-16 Australia/Sydney
From: Codex
To: Claude
Status: ready for read-only discovery

## Current state

- T-001“日志敏感信息脱敏加固”已完成、通过 Codex 只读审查并经人工验收。
- GitHub repository: `https://github.com/tudou0655-alt/wewriter`。
- Remote branch: `origin/main`。
- Current local branch: `codex/t001-github`，跟踪 `origin/main`。
- Current commit: `691ee09fab66e5a82fb0695da5e8930054799900`（`fix: harden sensitive log redaction`）。
- `origin/main` 已指向同一提交，推送使用普通快进，没有强制覆盖。
- 当前工作区在生成本交接文档前为干净状态。
- 本地旧 `master` 分支属于另一套历史，保留未删除；不得在没有人工明确批准时合并、重置、删除或强推。

## Completed work

- `wewriter/logs.py` 增强了 API Key、Authorization、OAuth token、微信读书 Cookie、异常 traceback 和参数化日志的脱敏。
- `tests/test_logs.py` 新增 10 项测试，覆盖 18 个泄漏场景及 11 个避免过度脱敏的反例。
- T-001 协作需求、计划、任务状态和验收记录已经建立。

## Verification evidence

| Check | Observed result |
|---|---|
| Python | 3.13.15 |
| Log-specific tests | 10 tests, OK |
| Full unittest suite | 121 tests, OK |
| Diff validation | `git diff --check` passed before integration commit |
| GitHub verification | `origin/main` and remote `refs/heads/main` both resolved to `691ee09fab66e5a82fb0695da5e8930054799900` |

## Known residual risks

- 日志脱敏只识别已登记的凭据字段；未来新增凭据名称时需同时扩展模式和测试。
- 无字段名的短 `sk-` / `sess-` 字符串不会脱敏，以避免误伤普通文本。
- 本地旧 `master` 与 GitHub `main` 没有共同提交历史，后续开发应以 `origin/main` / `codex/t001-github` 的当前历史为准，除非人工另有决定。
- `ui.py` 与 `weread.py` 体积较大；数据库迁移仍以 `user_version=1` 和启动时补列为主。
- 受限 Windows 环境下，测试需要将 `TEMP` / `TMP` 指向可写目录才能稳定运行。

## Next recommended task candidates

尚未批准新的实现任务。建议由人工从以下候选中选择后，再建立 T-002：

1. 建立版本化 SQLite 数据库迁移机制及迁移测试。
2. 固化 Windows/沙箱测试临时目录和统一验证入口。
3. 加强自定义 API 地址的密钥发送警告和域名确认。
4. 制定 `ui.py` / `weread.py` 的分阶段拆分计划；不要直接进行大规模重构。

## Exact next action

Claude 只读检查当前仓库、Git 状态和上述候选，向人工提供 T-002 建议及最小文件范围、验收标准和风险。人工批准前不得修改任何文件、不得创建或删除分支、不得提交或推送。

## Prompt for Claude

请进行下一任务的只读发现。先阅读 `CLAUDE.md`、`docs/collab/REQUIREMENTS.md`、`PLAN.md`、`TASKS.md` 和本 `HANDOFF.md`，再检查 Git 状态、当前分支、`origin/main` 和相关代码，但不要修改任何文件。T-001 已在提交 `691ee09fab66e5a82fb0695da5e8930054799900` 完成并推送。请比较以下候选：版本化 SQLite 迁移、测试临时目录稳定化、自定义 API 地址安全确认、超大模块分阶段拆分。推荐一个最小 T-002，写清收益、风险、精确文件范围、测试命令和验收标准。人工批准前不要实施，不要创建或删除分支，不要提交或推送，也不要读取真实凭据或调用真实 API。
