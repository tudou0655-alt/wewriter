# Current handoff — T-005 采集漏斗与跳过原因可视化（实现交接）

Updated: 2026-10-17

From: Claude

To: Codex（只读审查）

Task: T-005

Status: `done`，Codex 最终只读复审通过，人工已确认验收

## 最终验收结论

- Codex 于 2026-10-17 完成最终只读复审，未发现新的阻断问题。
- Codex 独立复跑专项测试 30 项、全量测试 161 项及离线交付检查 6 项，全部通过；`git diff --check` 通过，变更范围符合 T-005 获准的七个文件。
- 人工已确认 T-005 验收完成，并授权创建本地提交、普通推送 GitHub，以及重新构建 Windows 客户测试包。

## 开始前状态

- 分支 `codex/t001-github`，HEAD `b26971e`（`feat: harden delivery reliability`）。工作区只有 Codex 未提交的 T-004/T-005 立项修改（`TASKS.md`、`HANDOFF.md`），已保留；本文件下方的 Codex 立项说明和 T-004/T-003 记录原样保留。
- 人工在项目聊天中说“完成新任务”后开始实施。未创建或切换分支，未提交、未推送。

## 逐文件改动

1. `wewriter/db.py`
   - 新增 `_migrate_v3`，幂等地为 `jobs` 增加 `funnel TEXT NOT NULL DEFAULT ''`；`MIGRATIONS = {2: ..., 3: _migrate_v3}`，**数据库版本由 2 升为 3**。旧任务的 `funnel` 为空字符串。
   - `update_job` 允许写 `funnel` 字段。
2. `wewriter/engine.py`
   - 新增 `COLLECT_KEYS`、`MATERIAL_KEYS`、`FUNNEL_LABELS`，`Funnel` 类，以及 `load_funnel(raw)`、`format_funnel(raw)` 两个函数。
   - 口径（每篇文章在同一层只计一次）：
     - 采集层：`returned = deleted + out_of_window + invalid + cleared_blocked + repeated + candidates`。其中 `repeated` 指同一文章在列表中重复出现，包括跨公众号重复，只按首次出现的公众号计入候选。
     - 候选层：`candidates = below_threshold + used + over_limit + queued`，判定顺序与现有代码一致：先 `rank` 热度门槛，再排除既有稿件已用，最后截取单次篇数上限。
     - 素材层：每篇出队文章只计入 `material_cleared / body_failed / image_only / duplicate_body / usable` 中的一项。
   - 逐公众号：首轮按名称记录，已在审查修复中改为按唯一标识记录（见下文“审查修复”）。失败原因经 `logs.redact` 脱敏并截断到 200 字。全局计数等于各公众号计数之和（测试中逐项断言）。
   - 记录位置都在现有执行路径的分支上，只增加计数，不改变条件、顺序或返回值：
     - `_collect`：公众号失败（含 fatal，先保存再抛出）、各类过滤、候选、选文分层。每个公众号处理完和分层完成后都会保存，`hot` 模式“无达标文章”报错之前也已保存。
     - `_materials`：清理、去重、`ApiError`（`kind == 'image_only'` 计为图片型，其余计为正文获取或摘要失败）、成功。每篇处理完都保存，失败或中断时保留截至该点的统计。
     - `_write_one`：保存稿件后记 `drafts = 1`。
   - 重试：`run()` 在“运行中”重置处调用 `Funnel.for_attempt`，每次都从空漏斗开始，不会叠加。**假设**：如果本次沿用已保存的 `selected`（即重试时不重新采集），就保留上次采集层的数据并标记 `reused: true`，素材层和稿件数重新累计。界面会注明“本次重试沿用上次采集的选文”。没有采集统计的子任务显示“本任务沿用已保存的选文，没有本次采集统计”。
   - 批量（`separate_web`）：父任务负责采集层；新增 `_fold_children(parent_id, drafts)`，把各子任务最近一次尝试的素材层按公众号汇总到父任务，并写入完成稿件数。`run_separate` 结束时和 `refresh_parent`（单篇重试后重新计数）都会调用它。
   - `format_funnel` 先输出一句结果摘要，然后是全局阶段（主阶段始终显示，排除原因只显示非零项），最后是逐公众号明细，格式如“返回 N 篇，进入候选 N 篇，进入处理队列 N 篇；可用素材 N 篇；跳过：……”；采集失败的公众号显示“采集失败，<脱敏原因>”。空值或损坏的 JSON 返回“该历史任务没有采集统计”。
3. `wewriter/ui.py`：导入 `format_funnel`，在“运行记录 → 查看详情”的处理建议之后、警告之前插入“采集漏斗：”分段。没有新增控件或依赖。
4. `tests/test_collection_funnel.py`（新建，10 项）：两个公众号只有一篇可用（含精确展示文本）、公众号采集失败及脱敏、全部公众号失败仍保留统计、删除/无效/非字典/重复条目只计一次、已用过滤、热度门槛与篇数上限、图片型/正文失败/正文重复、写作失败后保留部分统计且重试不叠加、批量父任务汇总子任务素材、旧任务无统计（空值、None、损坏 JSON）。每项都会核对全局与逐公众号的计数是否一致。不导入 `test_core`，以便能用 `tests.test_collection_funnel` 点号形式单独运行。
5. `tests/test_db_migrations.py`：`assert_current_schema` 增加 `funnel` 列检查；新增 `test_v2_database_gains_funnel_column`（v2 库升级到 3，旧任务 funnel 为空，写入后再次启动保留），共 17 项。
6. `docs/collab/TASKS.md`：T-005 状态改为 `review`，勾选 14 项清单。
7. `docs/collab/HANDOFF.md`：本节。

## 审查修复（第 1 轮，P2：同名公众号的漏斗会被合并）

Codex 结论为“需修改”，P2 指向 `wewriter/engine.py:110-111`：`accounts` 以公众号名称为键，两个同名公众号的计数会合并。

- `wewriter/engine.py`
  - 漏斗数据格式 `version` 改为 2。`accounts` 的键改为 `'acct:' + accounts.identifier`（数据库中唯一），每项另存 `name` 和 `identifier`。
  - 新增 `owners`（文章 id → 列出它的公众号键）。在 `_collect` 分层时写入，重试沿用采集结果时一并保留。新增 `outcomes`（文章 id → 素材层结果）。
  - 新增 `Funnel.article_account(article)` 和 `Funnel.material(key, article)`。`_materials` 的五个分支都改为通过 `owners` 找到正确的公众号；没有 owner 的文章（批量子任务、基于原稿重新生成）回退到 `'name:' + account_name`。
  - `_fold_children` 不再按子任务的公众号名称汇总，而是读取子任务的 `outcomes`，通过父任务的 `owners` 归属到正确的公众号。
  - `format_funnel`：名称重复时显示为“名称（标识）”，名称不重复时显示不变。
- `tests/test_collection_funnel.py` 新增 3 项，共 13 项：
  - 同名公众号各自统计，展示文本带标识。
  - 同名公众号中一个采集失败，失败只记在它自己名下。
  - 批量任务中同名公众号的子任务素材按各自公众号归属。
  - 已有测试的断言键从名称改为 `acct:<标识>`。
- 范围：只改了 `engine.py`、`test_collection_funnel.py` 和两份协作文档，`db.py`、`ui.py` 本轮没有改动。

## 验证证据（Claude，2026-10-17，实际运行）

审查修复后复跑（当前有效结果）：

| Check | Command | Observed result |
|---|---|---|
| 专项测试 | `.\.venv\Scripts\python.exe -m unittest tests.test_collection_funnel tests.test_db_migrations -v` | `Ran 30 tests in 4.563s`，`OK`（13 + 17） |
| 全量测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 161 tests in 15.487s`，`OK`（147 + 14） |
| 离线交付检查 | `.\.venv\Scripts\python.exe scripts\verify_delivery.py` | “结果：6 项通过，0 项失败”，退出码 0 |
| 空白检查 | `git diff --check` | 退出码 0，只有 CRLF 提示 |
| 工作区 | `git status --short` | 与首轮相同，仍是 T-005 获准的七个文件 |

首轮结果（已被上表取代）：

| Check | Command | Observed result |
|---|---|---|
| 专项测试 | `.\.venv\Scripts\python.exe -m unittest tests.test_collection_funnel tests.test_db_migrations -v` | `Ran 27 tests in 3.777s`，`OK`（10 + 17） |
| 全量测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 158 tests in 15.692s`，`OK`（147 + 11） |
| 离线交付检查（额外） | `.\.venv\Scripts\python.exe scripts\verify_delivery.py` | 6 项通过，0 项失败，退出码 0；演示库版本为 3 |
| 演示 smoke（额外） | `.\.venv\Scripts\python.exe main.py --demo --data-dir .local-data\t005-demo --smoke-test .local-data\t005-smoke.json` | 退出码 0，`ok: True`，6 个页面通过（smoke 不会点击“查看详情”） |
| 空白检查 | `git diff --check` | 退出码 0，只有 CRLF 提示 |
| 工作区 | `git status --short` | ` M docs/collab/HANDOFF.md`、` M docs/collab/TASKS.md`、` M tests/test_db_migrations.py`、` M wewriter/db.py`、` M wewriter/engine.py`、` M wewriter/ui.py`、`?? tests/test_collection_funnel.py` |

## 剩余风险

- 数据库版本升到 3 之后，T-003 版本的程序（v2）打开新库会因 `NewerDatabaseError` 拒绝启动，这是 T-002/T-003 有意设计的行为，但会影响降级。
- 每处理一篇文章都会多一次 `UPDATE jobs SET funnel=...`。数据量很小，但在慢磁盘上会稍微增加写入。
- 首轮存在的同名公众号合并问题已修复。修复前写入的开发测试数据仍按名称归档（version 1），这类数据可以正常显示，但无法拆分。
- 失败原因只做 `redact` 和截断；如果上游错误文本里含有字段表之外的敏感字段，仍可能被记录（与 T-001 的已知限制相同）。
- “查看详情”窗口的实际排版没有人工查看过，只测试了 `format_funnel` 的文本。
- 判定顺序是热度门槛 → 既有稿件 → 篇数上限，这与代码顺序一致，但和清单中列出的顺序（“既有稿件”在“热度”之前）不同；同一篇文章只会归入它最先未通过的一层。

## Exact next action

P2 修复已完成，等待 Codex 最终只读复审：复跑上表命令，审查 `wewriter/engine.py` 的账户键、`owners`/`outcomes` 归属和新增的 3 项测试，给出“通过”或“需修改”。Claude 已停止编辑，没有提交。

---

# Codex 立项说明 — T-005（原文保留）

Status（立项时）: `todo`，已获人工批准，可开始实施

## 目标

让用户在“运行记录 → 查看详情”中直接看懂：扫描了多少公众号、每个公众号返回多少文章、文章在哪一步被排除，以及为什么最终只生成一篇。统计必须来自实际执行路径并持久化，不能通过事后猜测或普通警告文本拼接。

## 文件所有权

Claude 仅可编辑：

1. `wewriter/db.py`：结构化漏斗字段及版本迁移。
2. `wewriter/engine.py`：采集、筛选、素材处理和完成阶段的计数。
3. `wewriter/ui.py`：运行记录详情中的中文漏斗展示。
4. `tests/test_db_migrations.py`：数据库迁移与兼容测试。
5. `tests/test_collection_funnel.py`：漏斗计数、重试和展示测试，可新建。
6. `docs/collab/TASKS.md`：仅更新 T-005 状态和验收勾选。
7. `docs/collab/HANDOFF.md`：仅记录 T-005 实现结果、验证证据和交接。

当前工作区已有 Codex 未提交的 T-004 `TASKS.md`、`HANDOFF.md` 修改，必须保留。除上述七个文件外不得编辑；不得修改依赖、锁文件或构建配置。

## 数据与统计口径

- 使用结构化 JSON 持久化每次任务的漏斗；推荐新增专用 job 字段并通过下一顺序数据库版本幂等迁移，禁止塞入配置快照或把机器可读数据伪装成 warning。
- 全局统计至少包括：启用公众号、成功/失败公众号、接口返回、已删除、时间范围外、条目无效、已清理且不允许重采、合格候选、既有稿件已使用、热度门槛淘汰、处理队列、正文失败、图片型跳过、重复正文、可用素材、完成稿件。
- 每个公众号至少包括：名称或非敏感标识、接口返回、候选、各跳过原因、失败原因。失败原因必须经过现有安全边界处理，不保存凭据、Cookie、正文或 traceback。
- 统计层级互斥且命名明确；全局数应能由逐公众号数据和后续全局筛选解释。不要把“扫描两个公众号”表述为“返回两篇文章”。
- 重试任务前清空本次统计并重新累计，不能与上次结果相加；失败或中断时保留已完成阶段的数据。
- 旧任务没有统计时保持兼容，界面显示“该历史任务没有采集统计”。
- 只增加可观测性，不改变现有采集、选文、排序、去重、补位、模型调用或生成逻辑。

## UI 要求

- 在现有任务详情窗口增加“采集漏斗”分段，先展示一句结果摘要，再展示全局阶段和逐公众号明细。
- 对零值保持可读，避免堆砌无意义字段；但造成数量减少的零以外原因必须显示。
- 对“两个公众号最终一篇”能明确给出类似：公众号 A 返回 1 篇并进入处理；公众号 B 返回 1 篇但超出时间范围/已使用/正文失败；最终可用 1 篇。
- 不新增复杂图表库；使用现有 Tkinter 文本详情即可。

## 验收与安全边界

- 测试覆盖 `TASKS.md` T-005 清单规定的场景，并对精确计数和展示文本做断言。
- 不调用真实 API，不读取真实凭据，不安装或升级依赖，不新增依赖，不创建/切换分支，不提交、不推送。
- 测试只使用虚构公众号、文章和错误文本。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_collection_funnel tests.test_db_migrations -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
git status --short
```

完成后将 T-005 更新为 `review`，在本文件记录逐文件改动、数据库版本、测试数量、命令结果、剩余风险和精确 Git 状态，然后停止编辑，等待 Codex 只读审查。

## 给 Claude 的交接提示词

请实施 T-005“采集漏斗与跳过原因可视化”。开始前完整阅读仓库协作说明及 `docs/collab/REQUIREMENTS.md`、`PLAN.md`、`TASKS.md`、`HANDOFF.md`，检查 Git 状态并保留 Codex 尚未提交的 T-004 两份文档修改。将 T-005 标记为 `in-progress — Claude` 后，严格只编辑交接中列出的七个文件。用结构化、可迁移的数据记录每次任务从公众号返回、时间和有效性筛选、既有稿件/热度过滤到正文失败、图片型、重复正文、可用素材和完成稿件的漏斗，并在运行记录详情中以中文展示；旧任务兼容，重试不得累加旧统计。不得改变业务选择结果、增加网络调用、记录正文或凭据、安装依赖、提交或推送。完成专项与全量测试、空白和范围检查后，将状态改为 `review`，更新交接并停止编辑。

---

# Previous handoff — T-004 本地 Windows 测试包构建

## 任务范围

- 使用仓库已有 `scripts/build.ps1` 构建 WeWriter 0.10.3 Windows 测试包。
- 允许更新 `docs/collab/TASKS.md`、本文件，以及生成被 Git 忽略的 `build/`、`dist/` 产物。
- 不修改程序代码、测试、依赖、锁文件或构建配置；不安装依赖，不访问真实 API 或凭据，不提交、不推送。
- 构建脚本会清理并覆盖同版本的本地构建目录和 ZIP，这是本次人工请求生成“新的软件包”的预期行为。

## 构建与验证结果

- 构建命令：`powershell -ExecutionPolicy Bypass -File scripts\build.ps1`。
- 结果：退出码 0；PyInstaller 6.22.3、Python 3.13.15、Windows 11；构建成功。
- ZIP 内容正好为 `WeWriter.exe`、`install.ps1`、`README.md`。
- 打包后离线 smoke：`dist\v0.10.3\WeWriter.exe --demo --data-dir .local-data\packaged-demo --smoke-test .local-data\packaged-smoke.json`，退出码 0，`ok: true`，概览、公众号管理、文章资料库、稿件工作台、运行记录、设置六个页面全部通过。

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `dist\v0.10.3\WeWriter.exe` | 61,837,186 | `6EEF59B4BF51DD736099BC0F38E61CD20702C2751DD796E3E26FF1EEA3B3276E` |
| `dist\v0.10.3\install.ps1` | 1,059 | `D8CAD4097BCD33F3EC8571BB3772E45B9210F31B3C62011D307E66ED2220F702` |
| `dist\v0.10.3\README.md` | 20,948 | `A66C8BCBE2705246BDE76D1F392A86838E02783E8EFCEFCF0DA4C5043F3536D1` |
| `dist\WeWriter-v0.10.3-Windows.zip` | 61,260,254 | `D6F55205B84606E02A74B96C6C25291E860FC6572D22A8D322106DECCEC31830` |

## 限制与下一步

- EXE 的 Authenticode 状态为 `NotSigned`，Windows 可能显示未知发布者；正式客户交付前仍需代码签名或明确安装提示。
- 本轮只完成离线演示 smoke，没有使用真实 API、微信读书账号或客户数据。
- 下一步由人工解压 ZIP，在目标 Windows 环境运行 `install.ps1` 或直接启动 `WeWriter.exe`，按 `docs/交付验收清单.md` 完成人工项目。
- 本任务未提交、未推送；`build/`、`dist/` 和 `.local-data/` 产物均被 Git 忽略。

---

# Previous handoff — T-003 交付前可靠性收敛

## 最终结论

- Codex 于 2026-10-16 完成最终只读复审，未发现新的阻断问题。
- Codex 独立复跑专项测试 26 项、全量测试 147 项及离线交付检查 6 项，全部通过；`git diff --check` 通过，变更范围为 T-003 获准的九个文件。
- 人工已确认 T-003 完成，并明确授权更新为 `done`、创建本地提交及普通推送 GitHub。

## 开始前状态

- 分支 `codex/t001-github`，基于 `735efa9`。开始时只有 Codex 对 `TASKS.md`、`HANDOFF.md` 的 T-003 立项修改未提交，与实现文件没有重叠。
- 人工在项目聊天中说“继续新任务”后开始实施。未创建或切换分支，未提交、未推送。

## 逐文件改动

1. `main.py`
   - 新增 `startup_error_message(exc)`，按异常类型返回中文提示，不包含路径、凭据或 traceback：
     - `NewerDatabaseError`：提示安装最新版本。
     - 非 `OperationalError` 的 `sqlite3.DatabaseError`：提示文件可能损坏，从备份恢复。
     - 其他异常（权限、锁定、磁盘等）：提示检查磁盘空间、目录权限和占用。
   - 新增 `open_database(path, root)`：执行 `Database(path)` 和 `recover()`。失败时先用 `logging.getLogger("wewriter").exception` 记录日志（日志在 `logs.setup` 之后，经现有脱敏过滤），再 `messagebox.showerror`，然后 `root.destroy()`，返回 `None`。
   - `main()` 使用 `open_database`，失败时 `return 1`，不会构造 `Vault`/`App`，也不会进入 `mainloop`。入口改为 `sys.exit(main())`，其余路径的返回值仍是 `None`，退出码为 0。
2. `wewriter/db.py`
   - 新增 `NewerDatabaseError(ValueError)`，用于 `check_version` 拒绝较新版本的库和 `restore` 拒绝较新版本的备份。
   - 新增 `RESTORE_TABLES` 和 `_remove_sqlite_files(path)`（同时清理 `-journal`/`-wal`/`-shm`）。
   - `restore()` 改写为三步：
     1. 在正式库同目录用 `tempfile.mkstemp` 创建唯一临时文件 `<库名>.restore-*.tmp`。
     2. `_stage_restore`：对源备份以只读 URI 打开并做 `integrity_check`，然后 `backup()` 到临时文件。在临时文件上依次完成 `integrity_check`、`user_version` 校验（0 拒绝、超出版本拒绝）、关键表检查、`migrate_schema`、`recover`、关闭定时任务，最后再做一次 `integrity_check`。`sqlite3.Error` 统一转为不含路径的 `ValueError`。
     3. `_swap_in`：先对正式库执行 `wal_checkpoint(TRUNCATE)`，再执行 `journal_mode=DELETE`，确保 `-wal`/`-shm` 被合并并移除。如果无法切出 WAL（其他连接占用），就拒绝恢复。然后用 `os.replace` 做同目录原子替换；替换失败转为 `ValueError`。无论成败，最后都把正式库切回 WAL。
   - 临时文件在 `finally` 中清理；`prune_missing_images()` 只在替换成功后执行。源备份文件不会被修改。
3. `tests/test_db_migrations.py`：在原有 8 项基础上新增 6 项，共 14 项（审查修复后又新增 2 项，共 16 项，见下文）。首轮新增项覆盖：
   - 成功恢复：原子替换、关闭定时任务、源备份字节不变、journal_mode 为 wal、无临时文件残留。
   - 损坏备份、不完整备份、未来版本备份被拒绝。
   - 注入迁移失败（`patch.dict(MIGRATIONS)`）和注入 `os.replace` 失败。
   - 所有失败场景都断言：正式库的版本、账号、settings、表结构快照不变，`schedule_enabled` 仍为 True，`integrity_check` 为 ok，journal_mode 为 wal，无临时文件残留。
4. `tests/test_startup.py`（新建，6 项）：
   - 较新版本、损坏、权限/锁定三类提示的内容，以及提示中不含测试目录路径、虚构 `sk-` 值、`Traceback` 或盘符路径。
   - 真实的较新版本库会触发提示。
   - 正常打开时不弹窗。
   - `main()` 失败时返回 1，不构造 `App`/`Vault`，不进入 `mainloop`，并销毁 root。
   - 所有窗口与 messagebox 均被 mock，测试不会打开窗口。
5. `scripts/verify_delivery.py`（新建）：离线、确定性的 6 项检查，依次为 Python ≥ 3.13、12 个必需文件、`wewriter.__version__` 与 `build.ps1` 版本号一致、`requirements-lock.txt` 中 19 个锁定依赖的已安装版本一致、13 个模块可导入（包括 `main`），以及在 `.local-data/delivery-check` 临时目录中完成演示库初始化、`demo_data`、备份、恢复和版本校验。全部通过时返回 0，否则返回 1。输出经 `logs.redact` 脱敏，并把项目目录和用户目录替换为占位符。支持 `--root` 参数；脚本不联网、不调用 API、不读取凭据。
6. `tests/test_delivery.py`（新建，4 项）：
   - 仓库自检通过，且输出中不含项目路径。
   - 缺少文件时失败，并指出缺少的文件名、不泄露路径。
   - 版本号不一致时失败。
   - 检查函数抛出意外异常时会被报告为失败，而不是让脚本崩溃，输出中不含路径和虚构密钥。
7. `docs/交付验收清单.md`（新建）：分为【自动】【人工】【外部】三类，共四节：开发机自动检查 A1–A4、演示与打包 B1–B3、客户机人工验收 C1–C6、外部资源 D1–D4（真实 API、Edge 登录微信读书、Skill Key、代码签名）。结果列留空，不预填“通过”。
8. `docs/collab/TASKS.md`：T-003 状态改为 `review`，勾选了全部 14 项 T-003 验收清单。
9. `docs/collab/HANDOFF.md`：本文件。

## 审查修复（第 1 轮，P1：替换成功后 WAL 设置失败）

人工转达：请在 T-003 现有文件范围内修复 Codex 的 P1 意见，并补充“替换成功后 WAL 设置失败”的测试。我没有看到 P1 的原文，以下按这句描述理解并修复，请复审时核对是否与原意一致。

- 修复前的问题：`_swap_in` 的 `finally` 中直接执行 `journal_mode=WAL`，失败时会抛出异常。后果有两个：一是 `os.replace` 已经成功，`restore()` 却报失败，`prune_missing_images()` 也被跳过，用户看到“恢复失败”而数据其实已被替换；二是替换本身失败时，WAL 错误会掩盖真正的替换错误。
- `wewriter/db.py`：
  - 把切回 WAL 的操作提取为 `Database._enable_wal()`。`_swap_in` 用 `replaced` 标记替换是否完成，`finally` 中捕获 `sqlite3.Error`，只记录一条 `logger.warning`（“恢复完成/中止后未能切换回 WAL 模式，将在下次启动时重试”），不再抛出。这样替换成功时 `restore()` 正常返回并继续执行 `prune_missing_images()`；替换失败时，原来的 `ValueError` 不会被掩盖。下次 `Database()` 启动时会重新设置 WAL。
  - 顺带修改：替换前的 `wal_checkpoint`/`journal_mode=DELETE` 如果出现 `sqlite3.Error`（例如被锁），统一转为不含路径的 `ValueError`（“数据库正在被使用……原数据未改动”）。新增模块级 `logger = logging.getLogger('wewriter')`。
- `tests/test_db_migrations.py` 新增 2 项，共 16 项：
  - `test_restore_succeeds_when_wal_fails_after_replace`：注入 `_enable_wal` 失败后，断言恢复成功、数据为备份内容、定时任务已关闭、版本为当前版本、记录了警告、无临时文件、`integrity_check` 为 ok；此时 journal_mode 为 `delete`，再次 `Database()` 后恢复为 `wal`。
  - `test_wal_failure_does_not_mask_replace_failure`：同时注入替换失败和 WAL 失败，断言抛出的是“无法替换当前数据库”，正式库快照不变，无临时文件。

## 验证证据（Claude，2026-10-16，实际运行）

审查修复后复跑（当前有效结果）：

| Check | Command | Observed result |
|---|---|---|
| 专项测试 | `.\.venv\Scripts\python.exe -m unittest tests.test_db_migrations tests.test_startup tests.test_delivery -v` | `Ran 26 tests in 1.744s`，`OK`（16 + 6 + 4） |
| 全量测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 147 tests in 12.947s`，`OK`（129 基线 + 18 新增） |
| 离线交付检查 | `.\.venv\Scripts\python.exe scripts\verify_delivery.py` | 6 项通过，0 项失败，退出码 0 |
| 空白检查 | `git diff --check` | 退出码 0，只有 CRLF 提示 |
| 工作区 | `git status --short` | 与下方首轮结果相同，仍只有九个获准文件 |

首轮交接时的结果（已被上表取代）：

| Check | Command | Observed result |
|---|---|---|
| 专项测试 | `.\.venv\Scripts\python.exe -m unittest tests.test_db_migrations tests.test_startup tests.test_delivery -v` | `Ran 24 tests in 1.509s`，`OK`（14 + 6 + 4） |
| 全量测试 | `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` | `Ran 145 tests in 11.899s`，`OK`（129 基线 + 16 新增） |
| 离线交付检查 | `.\.venv\Scripts\python.exe scripts\verify_delivery.py` | 6 项全部 `[通过]`，输出“结果：6 项通过，0 项失败”，退出码 0。无额外离线参数 |
| 演示 smoke（额外） | `.\.venv\Scripts\python.exe main.py --demo --data-dir .local-data\delivery-demo --smoke-test .local-data\delivery-smoke.json` | 退出码 0；`delivery-smoke.json` 中 `"ok": true`，6 个页面：概览、公众号管理、文章资料库、稿件工作台、运行记录、设置 |
| 打包（额外） | `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` | **未执行**：会生成 `build/`、`dist/` 产物，且不是本轮代码通过的条件；留作人工交付门槛（清单 B2） |
| 空白检查 | `git diff --check` | 退出码 0；只有若干 `LF will be replaced by CRLF` 提示 |
| 工作区 | `git status --short` | ` M docs/collab/HANDOFF.md`、` M docs/collab/TASKS.md`、` M main.py`、` M tests/test_db_migrations.py`、` M wewriter/db.py`、`?? docs/交付验收清单.md`、`?? scripts/verify_delivery.py`、`?? tests/test_delivery.py`、`?? tests/test_startup.py`，正好是九个获准文件 |

开发过程中的实际失败：第一次运行 `verify_delivery.py` 时，`main.py` 有一处字符串换行写错，导致 `SyntaxError`，脚本按设计报告“模块导入”和“演示数据库”两项失败并返回 1。修正后复跑通过，上表结果均为修正后的运行结果。

运行产物：`.local-data/delivery-demo/`、`.local-data/delivery-smoke.json`、`.local-data/delivery-check/`（临时目录用后删除）。这些都在 `.gitignore` 的 `.local-data/` 范围内，只包含演示数据。

## 剩余风险

- `os.replace` 在 Windows 同卷 NTFS 上使用 `MoveFileEx(REPLACE_EXISTING)`，实践中是原子的，但 Windows 文档没有正式保证原子性。
- 恢复时如果其他线程或进程持有正式库连接，`journal_mode=DELETE` 无法切换，或者 `os.replace` 会因文件被占用而失败。这两种情况都会拒绝恢复并保留原库，但用户需要先关闭任务后重试。UI 侧已有 `require_idle()`。
- `_swap_in` 的 checkpoint 与 replace 之间存在极短的窗口，期间若有新连接打开，替换可能失败（会被安全拒绝），不会造成半恢复。
- 如果进程在 `journal_mode=DELETE` 之后、替换之前崩溃，正式库会停留在 DELETE 模式，内容不变；下次启动时 `Database.__init__` 会重新设为 WAL。
- `verify_delivery.py` 会导入 `main` 和 `wewriter.ui`，因此需要可用的 Tkinter；无 GUI 的 CI 环境未验证。
- 启动失败时的弹窗只用 mock 测试过，没有在真实窗口中人工查看过（清单 C3）。
- 本轮没有修改 `REQUIREMENTS.md`/`PLAN.md`。

## 未执行 / 仍需人工或外部验证

- 打包 `scripts\build.ps1` 和打包产物启动（清单 B2、B3）。
- 客户干净 Windows 机器上的安装、首次启动、异常提示、备份恢复、计划任务/休眠唤醒、托盘与单实例（清单 C1–C6）。
- 真实 API、Edge 登录微信读书、Skill Key、代码签名（清单 D1–D4）。开发方没有、也不应读取这些凭据。

## Exact next action

创建 T-003 本地提交，并以普通快进推送到 GitHub `main`；不得强制推送。
