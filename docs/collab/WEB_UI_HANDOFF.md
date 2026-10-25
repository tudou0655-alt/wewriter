# T-010 网页界面交接（Claude → Codex）

更新：2026-10-25 中国时间。实现者 Claude；审查者 Codex；最终验收者用户。本文件是 T-010 界面方向变更后的唯一权威说明，覆盖 `UI_IMPLEMENTATION_HANDOFF.md` 中“沿用 Tkinter、不换框架”的约定；其余功能要求（1—5 篇、审核分离、导出、无 Skill Key、无评论分析等）仍以那份文件为准。

## 1. 决策经过（按时间）

1. Claude 先按 `UI_IMPLEMENTATION_HANDOFF.md` 用 Tkinter 重写了 `wewriter/ui.py`（五页、功能接入完整，测试通过）。用户对照原型后指出布局不一致；Claude 重排后，用户评价“设计太丑了”，要求找 GitHub 上的 UI 设计 skill。
2. Claude 调研后说明：现有 UI skill（Anthropic `frontend-design`、`ui-ux-pro-max`）都面向网页；Tkinter 无法实现圆角、阴影、平滑字体，是“丑”的根本原因。给出三个选项：网页界面（pywebview）/ CustomTkinter / 继续打磨 Tkinter。
3. **用户决定（2026-10-25 10:08，原话）**：“我最终要整合打包到程序里的，用能实现最美观并且功能齐全的方案：一页一页先给我返回能交互试用的页面，我调整后最后一起做”。据此采用**网页界面**方案：先逐页做可交互试用页（示例数据），用户逐页确认；五页都确认后，再一次性整合进程序、接入真实功能并打包。
4. 设计方法采用 Anthropic `frontend-design` skill 的流程（先定 token 与版式、避开模板化默认、只在一处用力、保证焦点/减少动画/对比度）。未把该 skill 安装进任何配置，只是按其方法做。
5. 本轮其他已获人工批准的事项（之前已落地）：安装 `python-docx==1.2.0`（Word 导出）；导出增加 TXT；篇幅自由填写 200—20000；推荐模型 `deepseek-v4-pro`（文档处理）与联网搜索模型；2026-10-25 用户最终确定设置页只列四个模型：DeepSeek `deepseek-v4-pro`、`deepseek-flash`，OpenAI `gpt-5.6-terra`（均衡，$2/$12 每百万 token，支持 web_search）、`gpt-6-luna`（最便宜，$0.10/$0.50）；推荐写作/摘要 `deepseek-v4-pro`、联网搜索 `gpt-5.6-terra`。仅作为选项与推荐，未改用户已保存配置。

**尚未获批**：`pywebview` 依赖安装、PyInstaller 打包改动、`main.py` 入口改动。整合阶段开始前需向用户确认安装 pywebview（见第 6 节）。

## 2. 交付物与位置

| 页面 | 状态 | 仓库文件 | 用户试用链接（私有，仅用户可开） |
|---|---|---|---|
| 1 工作台 | 用户已确认（“挺好，下一页”） | `docs/collab/web-ui/workbench.html` | https://claude.ai/artifact/Egbwnx2UkeWHEcY7W1W2py |
| 2 公众号 | 用户已确认（“可以，每页最多十个”，已改为每页 10 个） | `docs/collab/web-ui/accounts.html` | https://claude.ai/artifact/5sxYL3dDNWuuFzMSX4ASZP |
| 3 文章库 | 用户已确认（“没问题”） | `docs/collab/web-ui/articles.html` | https://claude.ai/artifact/9LLr8MpCJiJWvijcQg5ZXF |
| 4 稿件库 | 用户已确认（“没问题”） | `docs/collab/web-ui/drafts.html` | https://claude.ai/artifact/Js4vQTNjfg5GSyhtQ9ncS1 |
| 5 设置 | 用户已确认（模型改为 GPT 两个：gpt-5.6-terra、gpt-6-luna；DeepSeek 两个） | `docs/collab/web-ui/settings.html` | https://claude.ai/artifact/XXa3cg8j7yPBn9AjFC4b7N |

- 每个 HTML 是**独立可交互页**：自带全部 CSS/JS，**示例数据写在页面脚本里**，没有任何网络请求（字体除外，见下）。用浏览器直接打开即可试用；文件开头没有 `<!doctype>/<html>/<body>`，因为发布平台会自动包一层，本地打开浏览器也能正常渲染。
- 字体目前从 Google Fonts 加载（Noto Sans SC / Noto Serif SC），**打包时必须改为本地字体文件**（OFL 授权，可随程序分发），否则客户离线时会退回系统字体（已写了回退栈：Microsoft YaHei UI / SimSun）。
- 原 `docs/collab/ui-prototypes/` 五个原型仍是“功能与结构”参考；新网页页面是“视觉与交互”的最终依据。

## 3. 设计系统（两页共用，后续三页沿用）

版式：左侧固定导航栏（196px）+ 右侧单列内容（最大宽 980px）；手机宽度（≤760px）导航变为顶部横条、内容单列。

颜色 token（浅色 / 深色，均在 `:root` 定义，深色在 `prefers-color-scheme: dark` 与 `[data-theme="dark"]` 下重定义）：

| token | 浅色 | 深色 | 含义 |
|---|---|---|---|
| `--ground` | #eef2f1 | #0e1416 | 页面底色 |
| `--sheet` | #ffffff | #162024 | 卡片/面板 |
| `--ink` / `--ink-2` | #15232a / #56656d | #e6eeeb / #9cb0ab | 正文 / 次要文字 |
| `--rule` | #d9e1df | #2a3a3e | 分隔线、边框 |
| `--jade` / `--jade-soft` | #0d6b5b / #e3f1ed | #63c6ae / #173a33 | **操作与进度**（主按钮、已完成、当前阶段） |
| `--cinnabar` / `--cinnabar-soft` | #b9452f / #f8e6e1 | #f08c74 / #3d2520 | **需要人处理**（待审核、失败、删除） |
| `--amber` / `--amber-soft` | #8a5a12 / #f7efdc | #e8c27a / #3a3020 | 提示/已停止/示例标记 |
| `--glint` | #8fe6d1 | #e6fff8 | 流光高光色 |

字体：标题 `Noto Serif SC`（宋体风格，页面标题 30px、摘要句 24px）；正文 `Noto Sans SC` 14px；数字用 `tabular-nums`。

**用户明确要求的规则：**
- **所有“正在进行”的元素都要有流光**（工作台：当前阶段文字与进度条、“生成中”标签、阶段说明文字、写作中的稿件标题及底部扫光线、运行中卡片描边；公众号：同步面板当前步骤、“正在读取书架”“正在等待扫码”）。完成/停止后流光停止；系统“减少动画”时改静态高亮。
- 选择器（文章范围、生成篇数）**收起时只显示选中项并放大**，不显示“更改”字样；点击展开全部选项，选后自动收起；点外部或 Esc 收起；数字居中、格子等宽。
- 历史任务**每页 5 条**；公众号列表**每页最多 10 个**。
- 页面里不用 `alert/confirm`，所有确认都在页面内（停止任务、删除公众号）。

通用组件：导航按钮、面板 `.panel`、主/次/安静/危险按钮、状态胶囊 `.chip`、开关 `.switch`（role=switch）、右侧抽屉 `.drawer`（Esc 关闭）、底部提示 `.toast`、分页 `.pager`、页内确认条、三步/五步进度条。

## 4. 页面说明与后端对应关系（整合时照此接线）

### 4.1 工作台（workbench.html）

| 区域 | 交互（页面内已实现） | 整合时数据来源 / 动作 |
|---|---|---|
| 新建任务 | 文章范围（今天 / 近 24 小时 / 近 3 天）、生成篇数 1—5（收起式选择器）、“N 个公众号已启用 · 按阅读量选文”、开始任务；运行中按钮变“加入队列”，提示“排队 N 个” | 配置键 `lookback_mode`（today/rolling）+ `lookback_hours`（24/72）、`draft_count`；`config.validate`；启用数 `db.accounts(enabled=True)`；开始/排队逻辑同 `ui.App.start_task / launch / next_queued`（排队任务 = jobs 行 status=等待中、stage=排队中、config 为冻结快照） |
| 当前任务 | 状态胶囊（生成中/等你审核/已完成/已停止）、一句总结（“1 篇待审核，1 篇处理中”，待审核用朱红）、“范围 · 目标 · 已用时”、五段进度 采集→预检→写作→二审→待审核（✓/序号）、阶段说明、查看详情、停止任务（仅运行中显示，页内确认，保留稿件）、取消排队 | 最近任务 `db.top_jobs(1)` 或当前运行 job；稿件 `db.task_drafts(job_id)` + `Database.review_state`；阶段 `ui.phase_index(job.stage)`（采集0 预检1 写作2 二审3 完成→4）；已用时 = updated_at/now − created_at；停止 `engine.stop()` 并暂停队列 |
| 详情抽屉 | 顶部“需要你注意”；“这次是怎么选出文章的”四步：①从 N 个公众号读到 X 篇（来源、是否都成功）②其中 Y 篇在时间范围内 ③去掉不能用的 Z 篇（原因）剩 W 篇 ④按阅读量选前 K 篇（列标题+阅读）；各公众号读取情况表；按钮“继续原任务 / 重新采集”各带一句说明 | `jobs.funnel` JSON：`counts.returned`→①；`returned − out_of_window − deleted − invalid − repeated − cleared_blocked`→②；`used`、`below_threshold`、`image_only`、`body_failed` 等→③的原因；`queued`/选中文章→④；`accounts.*`（name、counts.returned、latest_published、status、error、coverage）→表；继续原任务 `engine.run(run_key, 原config)`，重新采集 = 新建任务 |
| 本次稿件 | 序号、标题、状态（待审核朱红/已通过青绿）、提示（如“1 处引语待核实”）、查看稿件（写作中禁用、标题流光） | `db.task_drafts`；提示 `ui.draft_hint(report)`；查看 → 稿件库详情 |
| 历史任务 | 默认收起；每页 5 条；时间 · 状态胶囊 · “合格稿件 x / y” · 详情（失败/停止的详情顶部写原因） | `db.top_jobs(5, offset)`；合格 `db.qualified_drafts(job_id)`（只计人工“已通过/已审核”）；目标 = config.draft_count |

### 4.2 公众号（accounts.html）

| 区域 | 交互 | 整合时数据来源 / 动作 |
|---|---|---|
| 页头 | 手动添加、扫码同步书架；说明“名单来自微信读书书架（不是微信关注列表），启停删除从下一个新任务起生效” | — |
| 同步面板 | 三步进度：扫码登录 → 读取书架 → 选择导入。未登录：显示扫码说明（正式版二维码在 Edge 窗口里）、二维码过期刷新；已登录直接读取；读取中流光；失败：说明“名单没有改动”，可重试或重新扫码；选择：已在名单的置灰标“保留原启停状态”，新增默认勾选，全选新增 / 取消 / 导入所选；导入后新行短暂高亮 | 登录 `weread.browser_login`（Edge 扫码，状态写 vault `weread`）；读取 `WeReadSource.shelf()`（有 Skill Key 则走 Skill）；列表 `weread.shelf_accounts(books)`；导入 `weread.sync_books(db, books, selected=ids)`（按 MP_WXS_ 去重，保留启停）；失败类型 `ApiError.kind=='weread_auth'` 时提示重新扫码 |
| 手动添加 | 名称 + 文章链接；输入时实时识别账号编号（__biz），识别失败/重复给出原因 | `weread.identifier_from_link(link)` → `db.add_account(name, id)`；重复检查 identifier |
| 列表 | 搜索、全部/已启用/已停用筛选、总数；每页最多 10 个、分页；每行名称、采集状态（正常：上次读到 N 篇、最近一篇时间；失败：朱红；停用：已暂停采集）、开关、详情 | `db.accounts()`；状态来自 `ui.App.account_details()`（最近 30 个任务的 funnel.accounts）；开关 `UPDATE accounts SET enabled=1-enabled` |
| 详情抽屉 | 启用状态、最近一次采集（时间、结果或失败原因，注明“不代表全网最新发文”）、上次读到的文章、账号编号与来源、删除（页内确认：文章和稿件保留） | 同上；删除 `DELETE FROM accounts WHERE id=?`（不删文章/稿件） |

### 4.3 文章库（articles.html）

| 区域 | 交互 | 整合时数据来源 / 动作 |
|---|---|---|
| 筛选行 | 标题搜索；公众号下拉；发布时间（全部 / 今天 / 近 3 天 / 近 7 天，今天按中国自然日）；排序（阅读量优先【默认】/ 点赞量优先 / 最新发布）；“共 N 篇（文章库共 M 篇）”；说明“未知不等于 0，排在有数字的文章之后” | `articles`（cleared=0）LEFT JOIN `article_metrics`；排序 `(m.reads IS NULL), m.reads DESC, published_at DESC`（点赞同理），**先整体排序再分页**；时间比较用 UTC ISO（`published_at`） |
| 列表 | 每页 10 篇；标题、公众号、发布时间（中国时间）、状态（已用于生成稿件【青绿】/ 未使用 / 正文未获取【琥珀】/ 正文获取失败【朱红】）、阅读与点赞（未知显示“未知”）；按钮“查看文章”（无正文时为“查看详情”） | 已用 = `engine._used_articles()`；无正文 = `content=''`；失败 = `articles.error` 非空 |
| 阅读面板（右侧） | 公众号、标题、发布时间、大字阅读/点赞、状态说明（失败原因 / 尚未读取正文的原因 / 已用于稿件不再被选）、正文与原图位置、打开原文、可展开“采集详情”（阅读量来源、数字采集时间、文章采集时间、所属任务、原文链接） | 正文 `articles.content` + `db.article_images()`（本地图片路径）；来源 `metric_notes.source` / `METRIC_SOURCES`；时间 `article_metrics.observed_at`、`articles.fetched_at`；打开原文 `wechat_url()` 后用系统浏览器打开 |

### 4.4 稿件库（drafts.html）

| 区域 | 交互 | 整合时数据来源 / 动作 |
|---|---|---|
| 列表 | 搜索标题或正文；状态标签“全部 / 待审核 / 需修改 / 已通过”带数量；每页 10 篇、最新在前；行内显示状态（待审核朱红 / 需修改琥珀 / 已通过青绿）、生成时间（中国时间）、简短提示 | `drafts`（cleared=0）`ORDER BY id DESC`；旧值“已审核”按“已通过”显示与筛选（`Database.review_state`）；提示 `ui.draft_hint(report)` |
| 稿件视图 | 返回；状态胶囊（已通过时写“人工已确认（不代表已发布）”）；标题、生成时间、版本号、字数；提示条；正文排版（S/W 来源编号显示为上标，悬停说明导出时去掉） | `drafts.title/body/revision/updated_at`；字数不含来源编号 |
| 编辑 | 标题与正文可编辑；说明来源编号的作用；保存修改（标题/正文为空时拒绝）；取消编辑；**未保存离开保护**（返回、切页、关闭窗口时页内确认“继续编辑 / 放弃修改并离开”）；保存后新版本，已通过/需修改的稿件回到待审核并记入审核记录 | `db.edit_draft()`（文本变化自动回待审核、写 `draft_versions`） |
| 审核 | 标记需修改（可写一句原因，记入审核记录）；审核通过（页内确认，说明不会自动发布）；当前状态对应按钮置灰 | `db.set_draft_status(id, '需修改'/'已通过', note)`（只追加“【人工审核记录】”，不改写机器报告）；工作台合格数 `db.qualified_drafts` |
| 导出 | 格式 Word / Markdown / 纯文本；导出发布稿、导出素材报告；未通过时红字提醒但允许导出；取消保存时不提示成功；说明保存位置用设置里的默认位置，未设置则每次选择 | `ui.write_export(path, fmt, title, body, sources)`（无 S/W 编号）与 `ui.write_report(...)`；默认 `config.export_format/export_dir`；文件选择框由 pywebview `create_file_dialog` 提供 |
| 来源与审核提示（折叠） | 来源对应关系（S1/W1 → 标题与来源类型）；机器审核提示（保留原记录）；人工审核记录；历史版本 | `drafts.sources`、`drafts.report`（机器部分 + 追加的人工记录行）、`draft_versions` |

## 5. 已有代码状态（不要回退）

- 后端与数据层在 T-010 中已完成并测试：`config.py`（1—5、200—20000 字、lookback_mode、export_format/dir、评论停用）、`engine.py`（`window_start`、去掉评论调用）、`db.py`（`set_draft_status`、`qualified_drafts`、`task_drafts`、`top_jobs`、`review_state`、编辑后回待审核）、`weread.py`（`shelf_accounts`、`sync_books(selected)`、`identifier_from_link`）、`ui.py` 中可复用的纯函数（`export_document`、`write_export`/`write_report`（Word/MD/TXT）、`draft_hint`、`phase_index`、`compose_style`、`job_detail_text`、`china_time`）。
- `wewriter/ui.py` 的 Tkinter 界面（五页）已重写并可运行，但**视觉被用户否决**；在网页界面整合并验收前保留它作为可运行版本，整合后再决定是否删除。
- 测试：`unittest discover -s tests` 255 项 OK（含 `tests/test_t010.py` 16 项）；`scripts/verify_delivery.py` 6 项通过；`git diff --check` 通过。
- T-009（极致了主数据源、EveryInfra 备用、阅读量排名）仍 review，网页整合必须保留其行为。

## 6. 整合实际做法（2026-10-25 已完成，v0.10.4 包）

- 程序内页面在 `wewriter/web/`：五个独立 HTML（由试用页生成：去掉示例数据，导航与页脚由 `common.js` 的 `buildNav` 填）+ 每页一个脚本 `workbench.js / accounts.js / articles.js / drafts.js / settings.js` + 公共 `common.js`（`api()` 抛错、`call()` 出错弹提示、`confirmBox`、`go()` 离开前检查 `window.isDirty`）。没有合并成单页，页面之间直接跳转。
- `wewriter/webapp.py`：`Backend`（单工作线程、会话队列、停止即暂停队列、重启后排队任务不自动开始、定时检查每 30 秒）+ `Api`（`js_api`，方法名按页面前缀 `wb_ / acc_ / sync_ / art_ / dr_ / set_`，统一返回 `{ok, ...}` 或 `{ok: false, error}`，错误经 `redact`）+ `run()`（pywebview 窗口、pystray 托盘、关闭时询问是否最小化到托盘）。进度由页面轮询（忙时 1.5 秒），没有用 `evaluate_js` 推送。
- `main.py`：默认启动网页界面；`--tk` 或 pywebview/WebView2 失败时退回 Tk 经典界面；`--smoke-test` 仍走 Tk。
- WebView2 用 `private_mode=True`（临时配置目录）：之前用固定目录时，异常退出残留的 msedgewebview2 进程会占住目录，下次启动白屏（HRESULT 0x800700AA）。
- 字体仍从 Google Fonts 加载，但已改为不阻塞首屏（`media=print onload`）；离线或国内网络不通时直接用系统字体。**本地打包字体未做**（需要下载字体文件，待用户同意）。
- 依赖：requirements 加 `pywebview==6.2.1`，lock 加其依赖（bottle、proxy_tools、pythonnet、clr_loader、cffi、pycparser）；spec 加 `collect_all('webview')`、`clr` 等隐藏导入与 `('wewriter/web','wewriter/web')`。
- 已验证：五页在无头 Edge 中对真实 `Api` + 临时数据库跑通（开始任务、排队、详情、历史 5 条、书架同步导入、手动添加识别 __biz、文章排序未知值排后、编辑回待审核、审核通过、Word 导出、设置保存）；源码与打包 EXE 的真实窗口均能打开并显示演示数据；255 项单元测试、verify_delivery 通过。
- 2026-10-25 用户试用反馈后修正：① 自适应：内容区改为居中、最宽 1320px（设置页 1080px），左右留白 `clamp(16px, 3vw, 48px)`；窗口按屏幕可用区域定尺寸，小屏直接最大化（`webapp.window_size`）。② 发糊：`windows.set_dpi_aware()` 在创建任何窗口前设为 per-monitor v2；旧包实测是 system-aware（=1），窗口放在缩放比例不同于主屏的屏幕上会被位图拉伸；新包实测 =2。③ “联网资料降级”误报：`ui.draft_hint` 原来匹配任意“降级”字样，稿件审核提示里出现“应删除或降级为待核实”就误判；现在只认引擎写入的“## 联网研究状态”段。该次任务两篇稿件实际都完成了联网补充（W1—W5、W1—W2）。
- 2026-10-25 客户电脑任务反馈后修正：④ `api.Model.complete` 增加 `timeout`，写作与二审用 `LONG_OUTPUT_TIMEOUT=240` 秒（原 100 秒，2500 字长文会超时）；⑤ 二审修改稿未通过来源校验时，报错改为“二审修改稿未通过校验（原因），已弃用修改稿”，初稿照常保存（原文案“未保存”易误解）；⑥ 联网搜索失败属客户网络到 OpenAI 不通，未改程序；⑦ 侧边窗口左缘中部加固定收起箭头（`common.js` drawerTab，复用页面的 `#dw-close`）。测试 268 项通过。
- 未验证：真实扫码同步（网页版流程）、含模型调用的完整真实任务、125%/150% 缩放。

## 6a. 原整合方案（保留作对照）

1. 依赖：`pywebview`（Windows 使用系统 Edge WebView2 渲染）。**安装前需用户批准**；同步更新 requirements 与 lock、`WeWriter.spec`（打包 web 资源与字体）。
2. 结构建议：`wewriter/web/` 放单页应用（合并五页为一个 `index.html`，导航切换页面，共用设计系统 CSS）、本地字体 `fonts/*.woff2`；`wewriter/webapp.py` 提供 `Api` 类（`pywebview` 的 `js_api`），方法一一对应第 4 节的后端动作；长任务继续用现有单工作线程，进度通过 `window.evaluate_js` 推送到页面（替代示例里的定时器模拟）。
3. 页面去掉示例数据与“模拟扫码成功/模拟读取失败”按钮，改为调用 `Api`；“示例数据”标记删除。
4. 凭据只经 `secrets.Vault`；页面与日志不出现明文 Key；所有时间按中国时间（UTC+8）显示。
5. 入口：`main.py` 增加启动网页界面（保留 `--demo`、`--smoke-test` 语义）；托盘与单实例锁沿用。
6. 验收：五页真实流程（扫码同步、完整任务含模型调用、审核与三种导出、重启免扫码）、深浅色、125%/150% 缩放、离线字体。

## 7. 给 Codex 的审查要点

- 两个页面是否覆盖第 4 节列出的全部交互；文案是否从用户视角、无内部术语。
- 设计 token 是否在两页一致；深色模式下所有文字/背景都来自 token。
- 流光与“减少动画”是否成对实现；页内确认是否都存在，没有 `alert/confirm`。
- 第 4 节“整合时数据来源”是否与现有 `db/engine/weread` 函数签名一致（如发现不一致，请在 REVIEWS 中列出）。
- 未经批准，不应出现 pywebview 安装、打包改动或入口改动。
