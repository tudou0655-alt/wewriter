# Task board — 公众号信息获取及文章生成-源码-v0.10.3

Allowed status: `todo`, `in-progress — Codex`, `in-progress — Claude`, `review`, `blocked`, `done`.

| ID | Status | Owner | Scope/files | Acceptance check | Depends on |
|---|---|---|---|---|---|
| T-001 | done | Claude | 可编辑四个文件：`wewriter/logs.py`、`tests/test_logs.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | 虚构凭据脱敏测试、全量 unittest、`git diff --check`、范围与安全边界审查全部通过 | 已完成 |
| T-002 | done | Claude | 版本化 SQLite 迁移。可编辑：`wewriter/db.py`、`tests/test_db_migrations.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | v1 库升级、重复启动幂等、旧备份恢复后升级、拒绝更新版本备份、全量 unittest、`git diff --check` | 已完成 |

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
- 111 项 unittest 通过。
- Commit `d8f92fa`。
- 建立任务时 Git 工作区干净；本次仅新增协作文档修改，不包含程序代码修改。

## Rules

- One owner per task; one active task per agent.
- Claude 独占上述四个获准文件并遵守各自用途；Codex 仅在 Claude 交接后只读审查。
- Do not assign the same file to both agents at once.
- `done` requires observed verification, Codex review disposition, and human acceptance.
- 发现范围外问题时记录为风险，不顺手修改。
- 不提交、不推送、不更改远程状态，除非人工另行明确批准。
