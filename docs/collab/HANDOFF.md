# Current handoff — T-003 交付前可靠性收敛

Updated: 2026-10-16

From: Claude

To: Codex（一次性只读审查）

Task: T-003

Status: `done`，Codex 最终只读复审通过，人工已确认验收

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
