# Task board — 公众号信息获取及文章生成-源码-v0.10.3

Allowed status: `todo`, `in-progress — Codex`, `in-progress — Claude`, `review`, `blocked`, `done`.

| ID | Status | Owner | Scope/files | Acceptance check | Depends on |
|---|---|---|---|---|---|
| T-001 | done | Claude | 可编辑四个文件：`wewriter/logs.py`、`tests/test_logs.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | 虚构凭据脱敏测试、全量 unittest、`git diff --check`、范围与安全边界审查全部通过 | 已完成 |
| T-002 | done | Claude | 版本化 SQLite 迁移。可编辑：`wewriter/db.py`、`tests/test_db_migrations.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | v1 库升级、重复启动幂等、旧备份恢复后升级、拒绝更新版本备份、全量 unittest、`git diff --check` | 已完成 |
| T-003 | done | Claude | 交付前可靠性收敛。仅可编辑：`main.py`、`wewriter/db.py`、`tests/test_db_migrations.py`、`tests/test_startup.py`、`scripts/verify_delivery.py`、`tests/test_delivery.py`、`docs/交付验收清单.md`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两份协作文档仅限更新 T-003 状态、验收勾选、实现证据与交接 | 启动失败可操作提示、数据库原子恢复、离线交付检查、专项及全量测试、范围与安全边界检查全部通过；Claude 完成全部工作后由 Codex 一次性只读审查 | 已完成 |

## T-003 acceptance checklist

- [x] Claude 开始前检查 Git 状态，确认没有需要覆盖的他人修改，并将 T-003 状态改为 `in-progress — Claude`。
- [x] 只编辑 T-003 行列出的九个文件；其中 `TASKS.md` 仅更新 T-003 状态和本清单，`HANDOFF.md` 仅记录 T-003 实现结果、验证证据、剩余风险和交接。
- [x] `main.py` 捕获数据库初始化失败，向用户显示不泄露路径、凭据或 traceback 的可操作提示，安全终止本次启动，不进入部分初始化状态。
- [x] `wewriter/db.py` 在同目录临时数据库中完成恢复、版本检查、完整性检查和迁移；全部成功后才原子替换正式数据库，任一步骤失败时原数据库保持可用且内容不变。
- [x] 恢复逻辑继续拒绝 `user_version` 为 0 或高于当前程序支持版本的备份；错误不得导致正式数据库被覆盖。
- [x] 测试至少覆盖：启动数据库异常提示、恢复 v1 备份并升级到当前版本、拒绝未来版本、损坏备份、迁移或替换故障注入、失败后原数据库数据不变、临时文件清理。
- [x] `scripts/verify_delivery.py` 只做确定性的离线检查，不访问真实 API、不读取真实凭据；成功返回 0，缺少必要文件或检查失败返回非 0，并提供不含敏感信息的诊断。
- [x] `docs/交付验收清单.md` 明确区分自动化证据与人工/外部环境门槛；真实 API、客户干净 Windows 机器、Edge 登录、计划任务/休眠唤醒、代码签名均不得写成已验证。
- [x] 不安装或升级依赖，不改锁文件，不创建/切换分支，不提交、不推送，不访问外部服务；测试数据只使用明显虚构值。
- [x] `.\.venv\Scripts\python.exe -m unittest tests.test_db_migrations tests.test_startup tests.test_delivery -v` 通过。
- [x] `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 全部通过，数量不得少于当前 129 项基线加新增测试数。
- [x] `.\.venv\Scripts\python.exe scripts\verify_delivery.py` 通过；如脚本另有显式离线参数，须在 `HANDOFF.md` 记录实际命令。
- [x] `git diff --check` 通过，`git status --short` 仅显示 T-003 获准文件。
- [x] Claude 完成全部实现与验证后，将状态改为 `review`、更新 `HANDOFF.md`，然后停止编辑；Codex 随后进行一次性只读审查。
- [x] Codex 最终只读复审通过：专项 26 项、全量 147 项、离线检查 6 项全部通过，未发现新的阻断问题。
- [x] 人工确认 T-003 验收完成，并明确授权标记 `done`、创建本地提交及推送 GitHub。

## T-002 acceptance checklist

- [x] 开始前确认 Git 状态，并将状态改为 `in-progress — Claude`。
- [x] 只编辑上表 T-002 列出的四个文件。
- [x] 迁移步骤按版本号集中、有序、可重复执行；启动不再把 `user_version` 写死为 1。
- [x] 测试覆盖：缺列的 v1 库升级、新库、重复启动幂等、恢复 v1 备份后自动升级、拒绝高于当前版本的备份、拒绝打开更新版本的库且不产生任何结构或数据变化（第 1 轮审查修改）。
- [x] 现有数据（账号、文章、任务）在升级后保留。
- [x] `.\.venv\Scripts\python.exe -m unittest tests.test_db_migrations -v` 通过。
- [x] `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 在 121 项基线上增加新测试并全部通过。
- [x] `git diff --check` 通过，`git status --short` 未显示越界文件。
- [x] Claude 更新本文件状态为 `review` 并更新 `HANDOFF.md` 后停止编辑。
- [x] Codex 只读复审并给出“通过”；Codex 未编辑实现文件。
- [x] 人工确认验收通过并授权标记 `done`、本地提交及推送 GitHub。

## T-001 acceptance checklist

- [x] Claude 开始前确认 Git 状态，并将状态改为 `in-progress — Claude`。
- [x] 只编辑 `wewriter/logs.py`（程序实现）、`tests/test_logs.py`（测试）、`docs/collab/TASKS.md`（仅状态和勾选）、`docs/collab/HANDOFF.md`（仅结果、证据和交接）。
- [x] 除上述四个文件外没有编辑任何文件。
- [x] 测试只使用明显虚构的 API Key、Skill Key 和 Cookie，不读取真实凭据。
- [x] 覆盖 Bearer、`sk-`、URL/JSON/键值凭据、微信读书 Cookie、参数化日志和异常消息。
- [x] 保留 HTTP 状态、请求 ID、错误类型、文件名和普通文本。
- [x] 包含至少一个防止过度脱敏的反例。
- [x] `.\.venv\Scripts\python.exe -m unittest tests.test_logs -v` 通过。
- [x] `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 在 111 项基线上增加新测试并全部通过。
- [x] `git diff --check` 通过，`git status --short` 未显示越界文件。
- [x] Claude 更新本文件状态为 `review` 并更新 `HANDOFF.md` 后停止编辑。
- [x] Codex 只读审查并给出“通过”；Codex 未编辑实现文件。
- [x] 人工确认验收通过并批准标记 `done`、本地提交及推送 GitHub。

## Current verified baseline

- Python 3.13.15。
- 129 项 unittest 通过。
- Commit `735efa9`（`refactor: version SQLite schema migrations`）。
- Branch `codex/t001-github`，跟踪 `origin/main`；建立 T-003 前工作区干净。

## Rules

- One owner per task; one active task per agent.
- 每个任务的文件所有权以上表和对应验收清单为准；Claude 实施期间独占 T-003 获准文件，Codex 仅在 Claude 完整交接后一次性只读审查。
- Do not assign the same file to both agents at once.
- `done` requires observed verification, Codex review disposition, and human acceptance.
- 发现范围外问题时记录为风险，不顺手修改。
- 不提交、不推送、不更改远程状态，除非人工另行明确批准。
