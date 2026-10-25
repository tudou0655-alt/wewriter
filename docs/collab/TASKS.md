# Task board — 公众号信息获取及文章生成-源码-v0.10.3

Allowed status: `todo`, `in-progress — Codex`, `in-progress — Claude`, `review`, `blocked`, `done`.

| ID | Status | Owner | Scope/files | Acceptance check | Depends on |
|---|---|---|---|---|---|
| T-013 | in-progress — Claude | Claude | 按客户 2026-10-25 文章质量需求调整写作：读者（企业中高层/申报决策者）、目标（快速了解变化+可操作建议+与补贴直接相关的变化）、与参考文章关系（补充新信息并深化优先，其次同主题重组）、风格（专业但好懂、标题开头直白、历年数据对比）。可编辑：`wewriter/api.py`（写作/二审提示）、`wewriter/research.py`（检索重点）、`wewriter/ui.py`/`wewriter/web/settings.js`（风格预设）、`wewriter/config.py`（默认风格与迁移）、`tests/test_t013.py`、协作文档；追加：已用爆款“再写一篇”（`engine.rewrite_job`、`webapp.art_rewrite`、`web/articles.*`） | 离线测试：要求进入写作、二审、联网检索提示；默认风格迁移；全量测试。真实模型对比需用户授权 | 与 BUILD-002 并行：Codex 打包时源码可能含本任务改动 |
| BUILD-002 | in-progress — Codex | Codex | 人工授权打包当前源码；构建产物及协作文档 | 包检查、构建清单和校验值 | T-011/T-012 源码 |
| T-012 | review | Codex | 设置页一键清除后台数据；db.py、webapp.py、settings.html/js、专项测试 | 确认、自动备份、运行互斥、保留公众号/密钥/设置、清理校验 | 保留已有改动 |
| DATA-001 | done | Codex | 人工授权清空本机文章/稿件内容及其缓存，先备份；使用原生清理逻辑，保留任务历史、公众号、设置和凭据 | 清空计数、外键与完整性校验 | 无运行中任务 |
| T-011 | review | Codex | 人工授权：正文/摘要错误分离、摘要有限重试与安全诊断、网页漏斗口径修正；api.py、engine.py、webapp.py、相关网页与离线测试 | 缓存复用、重试上限/取消/不可重试错误、诊断脱敏、旧记录兼容及回归测试 | 保留 T-010 与其余未提交改动 |
| T-010 | in-progress — Claude | Claude | 五页 UI 与功能接入；2026-10-25 用户改为网页界面方案，五页已确认并整合打包为 v0.10.4 交用户测试（等待反馈），权威说明见 `docs/collab/WEB_UI_HANDOFF.md`（功能要求仍见 `docs/collab/UI_IMPLEMENTATION_HANDOFF.md`）；Codex 编写交接，后续只读审查 | 五页真实功能、书架同步、1—5 篇、模型配置、关闭评论分析、离线回归及桌面交互检查；人工验收后 done | 保留 T-006/007/008/009 未提交成果；T-008/009 原审查状态不变 |
| T-001 | done | Claude | 可编辑四个文件：`wewriter/logs.py`、`tests/test_logs.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | 虚构凭据脱敏测试、全量 unittest、`git diff --check`、范围与安全边界审查全部通过 | 已完成 |
| T-002 | done | Claude | 版本化 SQLite 迁移。可编辑：`wewriter/db.py`、`tests/test_db_migrations.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两者仅限任务追踪；Codex 后续只读审查 | v1 库升级、重复启动幂等、旧备份恢复后升级、拒绝更新版本备份、全量 unittest、`git diff --check` | 已完成 |
| T-003 | done | Claude | 交付前可靠性收敛。仅可编辑：`main.py`、`wewriter/db.py`、`tests/test_db_migrations.py`、`tests/test_startup.py`、`scripts/verify_delivery.py`、`tests/test_delivery.py`、`docs/交付验收清单.md`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两份协作文档仅限更新 T-003 状态、验收勾选、实现证据与交接 | 启动失败可操作提示、数据库原子恢复、离线交付检查、专项及全量测试、范围与安全边界检查全部通过；Claude 完成全部工作后由 Codex 一次性只读审查 | 已完成 |
| T-004 | done | Codex | 使用现有 `scripts/build.ps1` 生成本地 Windows 测试包；仅可更新 `docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`，以及生成被 Git 忽略的 `build/`、`dist/` 构建产物 | 构建退出码 0；EXE、安装脚本、README 和 ZIP 存在；ZIP 内容正确；记录大小与校验值；不安装依赖、不提交、不推送 | 已完成 |
| T-005 | done | Claude | 采集漏斗与跳过原因可视化。仅可编辑：`wewriter/db.py`、`wewriter/engine.py`、`wewriter/ui.py`、`tests/test_db_migrations.py`、`tests/test_collection_funnel.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两份文档仅限 T-005 追踪与交接 | 每个公众号及全局采集数量可解释；时间、已使用、无效、清理、热度、正文失败、图片型和重复正文等原因可见；数据持久化、迁移、重试和 UI 展示有测试；全量测试通过 | T-004 |
| T-008 | review | Claude | 文章质量。白名单见 `docs/collab/ARTICLE_QUALITY_HANDOFF.md`，按人工 2026-10-19 指示修正引用要求 | 数据流与报告腔来源有证据；二审风格上下文；干净导出不变；基准与离线评估；真实模型对照需授权 | T-007 |
| T-009 | review | Claude | 交付阻断：公众号历史列表 -2041。可编辑：`wewriter/weread.py`、`wewriter/engine.py`（覆盖标签、时间窗传递、EveryInfra 注入）、`wewriter/api.py`（EveryInfra.metrics、长 Retry-After 不重试）、`wewriter/ui.py`（设置页 EveryInfra 文案、极致了 Key 输入框）、`tests/test_weread.py`、`tests/test_core.py`、`README.md`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md` | 官方网页对照定位程序侧/接口侧；修复后真实运行多号、多篇、时间筛选；新进程重启无需扫码；不以提示、重试、降级成功作为解决依据 | 人工 2026-10-23 指示，暂停 T-008 |
| T-006 | done | Claude | 超出处理时间范围时显示公众号本次返回的最新文章发布时间。仅可编辑：`wewriter/engine.py`、`tests/test_collection_funnel.py`、`docs/collab/TASKS.md`、`docs/collab/HANDOFF.md`；后两份文档仅限 T-006 状态、验收和交接 | 专项 17 项、全量 165 项通过；Codex 只读审查通过，人工确认验收；固定中国时间显示 | T-005 |

## T-008 — 文章质量：报告腔来源、二审风格上下文与基准评审

- Status: `review`（离线部分完成；模型前后对照等待人工授权，质量交付门槛未通过前不得 done）；Owner: Claude；Depends on: T-007（review，等待 Codex 复审；T-008 不修改 T-007 的文件范围，只改 api.py 的写作/二审提示与输入）。
- Scope: 依据 `docs/collab/ARTICLE_QUALITY_HANDOFF.md` 的文件白名单，并按人工 2026-10-19 指示修正：发布稿保持干净、不强制展示编号，“导出去掉正文引用编号”不算缺陷；内部审核资料（稿件库正文中的 [S]/[W] 标记与素材报告）保留论述与来源的对应关系。人工指示优先于交接文件第 4 项“转成脚注”的要求。
- [x] 梳理原文、风格分析、联网资料进入初稿、二审和导出的数据流，并以证据说明报告腔来源（区分已证实与推断）。
- [x] 二审收到与初稿一致的 `style_reference`（及用户 style），明确事实准确性优先，不得为模仿风格编造内容。
- [x] 干净导出保持不变，内部来源对应关系保留；原有测试通过。
- [x] 以《稿件-10.md》建立只读基准（哈希）、固定参考资料包、配置和评分维度；离线评估脚本可重复运行。
- [x] 离线测试证明风格传到二审、导出未被破坏；全量 unittest 不少于 195 项。
- [ ] 文章对照评审（真实模型调用）须经人工授权后执行；未执行前不得宣称质量已改善，不标 done。

## T-007 — 客户交付可靠性与采集可信度（长任务）

- Status: `review`（B 阅读量差异根因 `blocked`：缺少脱敏原始证据，其余五项已按 Codex 第 1 轮 P1、P2 修正，待复审）；Owner: Claude；Depends on: T-006（done）。
- Scope: 六项需求及文件白名单见当前 HANDOFF.md；Codex 后续一次性只读审查。
- [x] 持久化诊断统一脱敏，虚构凭据不进入 warnings/message/相关诊断展示或新备份。
- [ ] 阅读量来源与字段有证据，真实 0、缺失、未知可区分；截图差异有解释与回归证据，未证实时不得勾选。（Claude：来源/状态/观察时间已记录并测试；截图差异未证实，保持未勾选）
- [x] 采集与子任务警告均保留；联网降级或质量警告不被父任务完成状态掩盖；重试不累加过时警告。
- [x] 逐公众号明确历史列表/最新一篇降级、覆盖限制及中国时间；不宣称已获取完整历史。
- [x] 继续原任务和新建重新采集操作分离；使用新配置/时间窗，保护完成稿件与去重规则。
- [x] 版本 0.10.4 来源统一，界面和产物可识别；构建记录明确提交及 dirty 状态。
- [x] 全量测试不少于 165 项且通过；专项、离线交付检查、空白与范围检查通过。
- [x] 保留 T-006 未提交实现；无真实 API/凭据访问，无依赖变更，无提交/推送/打包。
- [x] README 和交付清单覆盖全部六项，自动化证据与客户环境验收分开记录。
- [x] Claude 一次性交接逐项结果和剩余风险，状态 review 或明确 blocked，停止编辑等待 Codex。

## T-006 acceptance checklist

- [x] Codex 独立复跑专项 17 项、全量 165 项通过；人工确认验收并授权更新为 done。
- [x] 不改变文章选择、时间过滤、排序或生成逻辑，不增加网络请求。
- [x] 对每个成功返回文章的公众号记录本轮最新的有效发布时间。
- [x] 公众号存在“超出时间范围”文章时，在逐公众号漏斗中显示“最近返回文章：YYYY-MM-DD HH:MM（中国时间）”；统一使用 `Asia/Shanghai` / UTC+8，不依赖客户电脑时区。
- [x] 多篇乱序返回时选择真正最新的发布时间；无返回、采集失败、无超时文章和旧漏斗保持兼容。
- [x] 测试仅使用虚构文章和时间，不读取凭据、不调用真实 API。
- [x] `.\\.venv\\Scripts\\python.exe -m unittest tests.test_collection_funnel -v` 通过。
- [x] `.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v` 全部通过。
- [x] `git diff --check` 通过，改动仅限 T-006 获准的四个文件。
- [x] Claude 完成后将 T-006 更新为 `review`，在 `HANDOFF.md` 记录实现与证据并停止编辑；Codex 随后只读审查。

## T-005 acceptance checklist

- [x] Claude 开始前检查 Git 状态，保留 Codex 尚未提交的 T-004 文档修改，并将 T-005 状态改为 `in-progress — Claude`。
- [x] 只编辑 T-005 行列出的七个文件；不得覆盖或删除 T-004 的构建记录。
- [x] 为每次任务持久化结构化采集漏斗，不把统计混入配置快照或普通警告文本；如新增数据库字段，使用下一顺序版本的幂等迁移。
- [x] 全局至少显示：启用公众号数、成功/失败公众号数、接口返回数、时间范围外、已删除、无效条目、已清理且禁止重采、合格候选、已用于既有稿件、热度门槛淘汰、进入处理队列、正文获取失败、图片型跳过、正文重复、最终可用素材和完成稿件数。
- [x] 每个公众号至少显示：接口返回数、进入候选数和主要跳过原因；公众号采集失败时显示脱敏后的可操作原因。
- [x] 同一条文章在同一漏斗层级只计一次；全局汇总与逐公众号数据可核对，不用“扫描公众号数”冒充“返回文章数”。
- [x] 运行记录详情用易读中文分段展示漏斗；没有统计的旧任务仍能正常打开，并显示“该历史任务没有采集统计”。
- [x] 重试同一任务时重置并重建本次漏斗，不累加上次结果；任务中断或失败时保留截至失败点的统计。
- [x] 不改变现有选文、去重、采集、计费或生成行为；不新增网络请求，不记录正文、Cookie、API Key 或其他敏感值。
- [x] 测试覆盖：两个公众号只有一篇最终可用、公众号采集失败、时间范围过滤、已使用过滤、热度过滤、正文失败/图片型、正文重复、旧数据库迁移、旧任务无统计、重试不累加。
- [x] `.\.venv\Scripts\python.exe -m unittest tests.test_collection_funnel tests.test_db_migrations -v` 通过。
- [x] `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` 全部通过。
- [x] `git diff --check` 通过，`git status --short` 仅显示 T-004 已有两份文档修改和 T-005 获准文件。
- [x] Claude 完成后将 T-005 状态改为 `review`，更新 `HANDOFF.md` 并停止编辑；Codex 后续只读审查。

## T-004 acceptance checklist

- [x] 开始前确认 Git 工作区干净，当前提交与 `origin/main` 一致。
- [x] 使用现有 `scripts/build.ps1` 完成构建，不修改程序代码或构建配置。
- [x] `dist\v0.10.3\WeWriter.exe`、`install.ps1`、`README.md` 以及 `dist\WeWriter-v0.10.3-Windows.zip` 存在。
- [x] ZIP 仅包含预期的三个交付文件，生成文件大小和 SHA-256 已记录。
- [x] 不安装或升级依赖，不读取凭据，不运行真实 API，不提交、不推送。
- [x] 更新 `HANDOFF.md`，如实记录构建结果、测试限制和用户下一步。

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

## RESUME-EN - 2026-10-24
- Status: in-progress — Codex; Owner: Codex.
- Human request: rewrite the supplied v7 Chinese resume in English. Independent document task; scope: output/pdf and tmp/pdfs only, plus append-only task/handoff records. No application changes.

- RESUME-EN completion: done — Codex. One-page English PDF and editable UTF-8 text produced; PDF rendered and visually inspected.


- RESUME-EN-PHOTO: in-progress — Codex. Human requested retaining the original portrait in the English resume; scope: resume PDF and generation helper, append-only tracking records.

- RESUME-EN-PHOTO: done — Codex. Original embedded portrait retained in top-right header; regenerated PDF confirmed one page and visually checked.
