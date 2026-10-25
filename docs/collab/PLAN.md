# Current implementation plan — T-007

Status: approved；Claude 独占实现，Codex 完成交接后一次性只读审查。

1. 阅读四份协作文档，记录 Git 状态、HEAD、已有 T-006 diff；将 T-007 标为 in-progress — Claude。不得覆盖未提交的 T-006。
2. 先为已确认问题建立离线回归证据，完成诊断脱敏；再调查指标数据契约，记录证据与未确定项。
3. 完成阶段警告传播、采集覆盖标记、继续任务与重新采集分离，统一中国时间展示。
4. 将版本提升至 0.10.4，统一版本来源与构建标识，补齐 README、交付验收清单及离线校验。
5. 运行专项、全量及离线交付检查，审查 diff 范围；记录六项逐条结论、代码位置、实际命令和结果，以及需要客户验证的项目。
6. 全部可实施工作完成后统一交接。无阻塞时 T-007 → review；若指标契约缺失等问题阻塞验收，完成其余独立项后 T-007 → blocked，列出精确所需证据，不能把未解决项勾成完成。

所有权和允许编辑范围以当前 HANDOFF.md 为准。允许在获准文件内进行必要最小实现；不做无关重构。仅修改源码、测试、构建脚本及文档，不执行构建、不提交、不推送。下方计划是历史记录，不再限制 T-007。

# Historical implementation plan — T-001 日志敏感信息脱敏加固

Status: completed
Planner: Codex
Approved by: human request on 2026-10-16

## Approach

由 Claude 在 `wewriter/logs.py` 内集中加固日志消息脱敏，并在新文件 `tests/test_logs.py` 中使用明显虚构的值建立表驱动测试。保持现有日志轮转、异常钩子和输出格式不变。实现完成后由 Codex 只读检查 diff、测试证据和安全边界，不编辑文件。

## File ownership

| Phase | Owner | Access | Files |
|---|---|---|---|
| Implementation | Claude | May edit | `wewriter/logs.py`（程序实现）、`tests/test_logs.py`（测试）、`docs/collab/TASKS.md`（仅更新 T-001 状态和验收勾选）、`docs/collab/HANDOFF.md`（仅记录实现结果、验证证据和交接） |
| Review | Codex | Read-only | Repository diff and relevant tests; no file edits |
| Approval/merge | Human | Decision | Any commit, push, merge or scope change |

Claude 只能按上表编辑四个文件，除此之外不得编辑任何文件；如发现必须扩大范围，应停止并交回人工决定。Codex 审查阶段不得修复问题，只能给出通过或需修改结论及证据。

## Phases

### Phase 1 — Claude implementation

- Goal: 加固日志敏感值脱敏，同时保留有效诊断上下文。
- Files/components: `wewriter/logs.py`、`tests/test_logs.py`，以及仅用于规定任务追踪更新的 `docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`。
- Safety boundary: 不读取真实凭据；不调用真实 API；不安装依赖；测试仅使用明显虚构的密钥和 Cookie。
- Validation:
  - `.\.venv\Scripts\python.exe -m unittest tests.test_logs -v`
  - `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
  - `git diff --check`
  - `git status --short`
- Exit criteria:
  - 新增测试覆盖 Bearer、`sk-`、API/Skill Key 字段、微信读书 Cookie、参数化日志、异常消息、保留诊断字段和防止过度脱敏。
  - 所有测试使用明显虚构值，过滤后不可恢复原始敏感值。
  - 全量测试在现有 111 项基础上增加新测试并全部通过。
  - Claude 新增的 diff 仅涉及获准的四个文件；仓库总 diff 还可包含 Codex 此前建立任务时对 `REQUIREMENTS.md`、`PLAN.md` 的修改。
- Human gate: 若需修改其他文件、读取凭据或改变依赖，立即停止并请求批准。

### Phase 2 — Codex read-only review

- Goal: 独立核对实现是否满足 R-001 至 R-006。
- Access: 只读；不得修改或格式化任何文件。
- Review evidence:
  - 核对 `git diff` 和 `git status`；
  - 阅读实现与测试；
  - 复跑上述两条测试命令和 `git diff --check`；
  - 确认没有真实凭据、外部调用、依赖变化或越界文件修改。
- Exit criteria: 给出“通过”或“需修改”，列出代码证据、测试证据和剩余风险。

## Decisions

| Date | Decision | Reason | Consequence |
|---|---|---|---|
| 2026-10-16 | T-001 由 Claude 实现、Codex 只读审查 | 首次双 AI 协作需明确文件所有权并避免并发编辑 | 实现与审查职责分离 |
| 2026-10-16 | 只允许两个实现文件 | 控制首次任务范围和回归风险 | 任何额外文件都需要新批准 |
| 2026-10-16 | 仅使用明显虚构凭据 | 避免测试或日志接触真实秘密 | 不以真实环境数据作为验收条件 |

## Rollback/recovery

本任务不允许提交或改写 Git 历史。如实现未通过审查，由 Claude 根据审查意见在获准的四个文件及各自限定用途内继续修正。需要撤销时由人工审阅精确 diff 后决定；任何破坏性 Git 操作仍需单独批准。
