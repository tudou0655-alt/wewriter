# Task board — 公众号信息获取及文章生成-源码-v0.10.3

T-059 — in-progress — Codex；按人工要求将新版程序和公开演示视频同步 GitHub，移除旧演示图片；公开视频中的公众号、文章、稿件名称必须使用虚构演示用例。当前先核查素材与发布范围，保留 T-057/T-058 既有未提交记录。

T-058 — done — Codex；人工要求提供可直接运行的 EXE 路径。已从现有最新版 `dist/T054/WeWriter-v0.10.4-Windows.zip` 提取 `dist/T054/WeWriter.exe`（77,653,990 字节，SHA-256 `1EDA38FED25651981E5064BED41DDAA0CE32E563E07B0FB8631267D5F3205BFB`）。打包 EXE 在免 Key demo smoke 模式退出码 0、结果文件 `ok=true`、六页加载；不代表真实接口或客户机验收。未覆盖历史简约版包，未改程序代码、提交或推送。

T-057 — done — Codex；GitHub 历史提交 `10a710f` 的简约版界面仍在，已从该干净提交的完整程序单独打包到 `dist/T057-simple-10a710f/WeWriter-v0.10.4-Windows.zip`（69,065,364 字节，SHA-256 `F9D7ED05790AE6FC78E64A6FF10CBAF980BB3772C28DD668E9187B638178F5A4`）。构建清单 `0.10.4+g10a710f`、dirty=false；离线交付检查 6 项、包内容校验及打包 EXE 免 Key demo smoke（6 页）通过。独立构建副本已清理，当前 `main` 源码与 `dist/T054` 包未改；无真实接口、凭据访问、提交或推送。此包是历史完整版本，不含后来加入的双入口工作台及其他新功能；未签名，客户环境需自行试用。

T-056 — done — Codex；人工要求把当前代码与界面同步到 GitHub 并更新 README。已将 84 个源码、测试、文档及界面资源文件提交为 `3fa0081` 并推送至 `origin/main`；README 反映双入口工作台、草稿优先、当前采集/模型配置和测试限制，`.gitignore` 排除本机 `tmp/`、`output/`。交付检查 6 项和主要前端 JS 语法通过；499 项非浏览器测试有 8 失败、1 错误，两组浏览器测试本机启动停滞，已如实写入 README；真实客户验收及测试修复留后续任务。提交范围未包含运行数据、临时输出、安装包或凭据。

T-055 — done — Codex；按人工要求清理 `dist/` 旧安装 ZIP、旧源码归档副本和各版本解包目录。清理前逐项确认目标路径属于 `dist/`，并核对保留包哈希；清理后 `dist/` 仅有 `T054/WeWriter-v0.10.4-Windows.zip` 一个文件（77,083,564 字节），SHA-256 仍为 `A8C8D7A15AFDF08EA67D4AA38B3613DF480D8FA1320513488B307A83BC2BBC3F`。未动源码、正式数据、`build/`，未提交/推送。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工明确清理要求冲突，以本轮要求为准。

T-054 — review — Codex；已定位 Luna 费用待核对是缓存写入用量读取层级错误。按官方 Responses 结构读取 30 分钟/12 小时缓存写入明细；仅在新模型计入写入溢价，聚合用量依请求默认 TTL 计价；旧记录缺明细时继续标未知并在界面说明。未改正式库，旧任务不能精确追算。264 项离线单测、JS 语法/Python 编译、交付检查 6 项、`git diff --check`、PyInstaller 构建与包校验通过。独立包 `dist/T054/WeWriter-v0.10.4-Windows.zip` SHA-256 `A8C8D7A15AFDF08EA67D4AA38B3613DF480D8FA1320513488B307A83BC2BBC3F`；待人工安装并运行新任务核验。未调用付费 API、读取请求正文或凭据、提交/推送。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工排错修复要求冲突，以本轮要求为准。

T-053 — done — Codex；已将当前含未提交改动的 156 个源码、测试、文档及界面资源文件打包至桌面 `公众号信息获取及文章生成-源码-2026-10-28.zip`（12,204,163 字节）。排除 Git 元数据、虚拟环境、旧构建/安装包、运行数据、临时输出及凭据；ZIP CRC 与每个原文件 SHA-256 一致，桌面副本 SHA-256 `623C522C1E5402341C55C0CF76FA33442B3D927C2C803F52E4F5B8E30F7846E6`。保留既有改动，未提交/推送或调用外部服务。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工明确导出要求冲突，以本轮要求为准。

T-052 — review — Codex；人工截图反馈深色主题下主页面“生成文章”按钮浅底浅字。已将该按钮背景、文字和边框固定为浅底深字，不再随深色主题继承浅色文字；计算对比度约 15.06:1。仅改 `wewriter/web/styles/pages/workbench.css` 业务样式；交付检查 6 项、`git diff --check`、PyInstaller 构建与包内容校验通过。独立包 `dist/T052/WeWriter-v0.10.4-Windows.zip` SHA-256 `F5C5DC823D3185B26E85DD7F8C157564CE3EC4E9B8E4143F64F9CB9EF713A5CD`，未覆盖 T051；真实界面视觉待人工复看。保留既有未提交改动，未调用真实 API、改正式库或提交/推送。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工明确修复要求冲突，以本轮要求为准。

T-051 — review — Codex；工作台主区域拆为“采集素材”和“生成文章”两个按钮。采集只同步文章信息及阅读量，不调用摘要/写作模型；写作从本地未用素材中按阅读量选文，阅读量未知且门槛为 0 时排在已知数据后面，不重新读取公众号列表。已保存正文/摘要继续复用，缺少正文才按需读取；旧任务继续原选文。每日定时改为只使用本地素材，不自动采集。新增 4 项专项测试，连同相关回归 20 组共 260 项离线通过；2 项既有测试仍与 T037 预检冲突，浏览器界面测试在本机启动阶段停住，真实 GUI/DS 待人工验证。JS 语法、Python 编译、交付检查 6 项、包内容及 `git diff --check` 通过。独立包 `dist/T051/WeWriter-v0.10.4-Windows.zip` SHA-256 `B723C9BFCFC9BF075905EADFEBF4E935556D5865CC024492F2BC533E95FCD57D`。未读取凭据或调用真实付费 API、未改正式库、未提交/推送。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工明确实施要求冲突，以本轮要求为准。

T-050 — review — Codex；人工要求优先“让程序能正常走完一个流程”，再优化文章质量。已增加默认开启的“优先生成草稿”：精简写作提示；短稿可保存并标注待补充；无效来源编号从正文移除、标记人工核对；跳过自动二审、联网核验与风格改写，减少付费请求和单条规则导致整轮失败。旧严格模式可在设置中关闭该选项恢复。新增 4 项草稿优先测试，连同 T050 补段、T048 篇幅及相关回归共 19 组 256 项离线通过；JS 语法、Python 编译和交付包检查通过。独立包 `dist/T050/WeWriter-v0.10.4-Windows.zip`，SHA-256 `87E1EC3211628A925359EF6FCDFDCA9F4FA2F6C84D0F0AE693385300A962F8F0`，待人工真实 DS/GUI 测试。旧 T013 另有 2 失败 3 错误（旧提示词期望、主题资源及缺少 webview），另两项旧测试与 T037 预检冲突；完整套件未宣称通过。未读取正文/凭据、调用付费 API、改正式库、提交或推送。旧 REQUIREMENTS/PLAN T-007 与本轮明确修复要求冲突，以人工要求为准。

T-049 — review — Codex；人工测试 T048 包后反馈失败。只读正式库确认子任务 7 仅选中文章 ID 12，写作模型使用 `[S1]`、`[S10]` 等无效编号而程序要求 `[S12]`，两次校验失败、无成稿。将素材引用号改为本篇从 `[S1]` 连续编号，文章数据库 ID 仍独立保留；写作请求和重试反馈明确列出允许的来源编号并区分 F 事实序号。新增 2 项专项测试复现 ID 12 场景与引用重试；相关 10 组 149 项离线测试通过，`git diff --check` 通过。独立包 `dist/T049/WeWriter-v0.10.4-Windows.zip` 校验通过，SHA-256 `838DE86C033FA33C168B5F3D69D96823A018D5952E02F004EEEAA1F465D4DE0B`；真实 DS/GUI 待人工复测。未读取正文/凭据、调用付费 API、改正式库、提交或推送。旧 REQUIREMENTS/PLAN T-007 只读范围与本轮人工明确修复要求冲突，以本轮要求为准。

T-048 — review — Codex；人工实测 DeepSeek 目标 2000 字，先后产出约 475/586 字。写作提示改为正文目标 90%–110%，首稿低于 90% 时最多定向重写一次，二审及风格改写后按工作台同口径守住 90% 下限；仍不足时提示素材不足并拒绝存短稿，避免重复和编造。阶段写作缓存换键，避免旧短稿断点被继续复用。新增 7 项离线测试通过；文章质量 31、风格卡 29、可读性 14、独立成稿 8、T040 安全 12、保密 13 项通过，`git diff --check` 通过。T013 旧提示词期望 2 失败 1 错误（与本改动无关，未按旧规则回退）。独立测试包 `dist/T048/WeWriter-v0.10.4-Windows.zip` 已校验，SHA-256 `7C8F2BDA172240391F04796BB9C6A84533492380F7BF8915DD5C25437965CA9F`；真实 DS 及 GUI 留人工测试。保留现有未提交改动，未读取凭据、调用付费 API、改正式数据库、提交或推送。旧 REQUIREMENTS/PLAN 的 T-007 只读范围与本轮人工明确修复要求冲突，以本轮要求为准。

T-047 — review — Codex；人工要求 DeepSeek 原生搜索与文本生成作为默认，OpenAI GPT 和阿里云百炼作为可选备选。已实现 DeepSeek Search 结构化检索（最多两次主题/标识查询，自动查证时取网页正文）、DeepSeek 默认摘要与写作、百炼 Qwen3.8-Flash 和 GPT 手动可选，同服务商摘要与写作可选不同模型。按服务商、模型、缓存 token、峰谷/长上下文及实际搜索次数估算费用；官方基础费率与适用条件见 T047_RATES.md。8 组关键专项 109 项通过，JS 语法、离线交付检查、独立包校验和 `git diff --check` 通过；完整 GUI/真实模型调用留人工测试。独立测试包 `dist/T047/WeWriter-v0.10.4-Windows.zip`，SHA-256 `C0216C1AB8FAA3541722B9B7F2E17A8017E2B0960AEA14702AB6FB490F0CB721`，未覆盖旧包。未调用真实付费 API、读取凭据、改正式数据库、提交或推送。旧 REQUIREMENTS/PLAN 的 T-007 只读限制与本轮人工明确实施要求冲突，以本轮人工要求为准。

T-046 — done — Codex；人工询问国内模型 API 联网能力。只读核对官方文档：DeepSeek 已提供 `/v1/tools/search` 与 `/v1/tools/fetch`（各 5 元/千次成功请求，复用 DeepSeek Key），V4-Pro Responses 也支持服务端 web_search；阿里云百炼北京地域 Qwen3.8-Flash 等支持联网，Responses agent 搜索 4 元/千次，Qwen3.8-Flash 输入/输出 0.8/2.7 元每百万 token；百度千帆独立 Web Search API 官方页面列每日 100 次免费额度；Kimi 官方提示自身 web_search 正在升级、暂不推荐。当前程序 `wewriter/research.py` 与 `pipeline/research.py` 固定 OpenAI Responses 及其引用结构，不能仅改模型名/地址接入上述国内服务；后续需适配来源引用、计费、缓存、断点与事实核验。以 T-045 历史 28 次为等量示意，DeepSeek 裸搜索约 ¥0.14、百炼北京 agent 搜索约 ¥0.112，均不含模型 token/抓取，实际请求量与结果质量需测试。未修改业务代码、正式配置、凭据或调用付费 API。旧 REQUIREMENTS/PLAN 的 T-007 只读范围与本轮直接调查指令冲突，以人工本轮要求为准。

T-045 — done — Codex；人工询问更便宜的联网搜索模型与当前成本。只读核对新任务 job 2、3 费用记录，四次 OpenAI Responses 联网研究/查证共 28 次 web_search，142,594 输入 token、22,071 输出 token；按程序记录的 $0.01/次搜索、旧模型费率与示意汇率 6.70，搜索费约 $0.28、文字处理约 $0.078、合计约 ¥2.40。OpenAI 官方文档确认 `gpt-6-luna` 支持 Responses web_search，标准价输入 $0.10/百万 token、输出 $0.50/百万 token，搜索工具另收 $10/千次；若调用量不变，代入历史 token 用量约 ¥2.04，仅作条件推算。设置已有 Luna 选项，但 `wewriter/costs.py` 缺其费率，界面可能显示未知费用。主要优化是限制单次任务的搜索工具调用并收紧查证范围；未修改源码、设置或调用付费 API。旧 REQUIREMENTS/PLAN 的 T-007 范围与本轮直接调查指令冲突，以人工本轮要求为准。

T-044 — done — Codex；人工要求排查“目标两篇只生成一篇”。只读检查清空后新建的正式库：父任务 1 配置 `separate_web`、`draft_count=2`，4 篇候选；热度第 1 篇对应子任务 2 在“综合写作”失败，错误码 `model_output`、信息“模型未返回有效 JSON，请重试或切换模型”，该子任务已付费完成摘要与联网研究并发出两次写作模型请求，未存稿；热度第 2 篇子任务 3 成功保存一篇，故父任务“部分完成 1/2”。失败子任务的摘要与联网研究缓存存在，完整写作断点不存在；原任务继续会重试失败子任务，不重复生成已完成稿件，但会重新产生写作模型费用。未保存模型原始输出，因此无法断言首次校验错误或最终 JSON 的具体语法问题；未调用真实 API、修改数据库或业务代码。旧 REQUIREMENTS/PLAN 的 T-007 只读范围与这次明确调查任务冲突，以本轮人工要求为准。

T-043 — done — Codex；人工明确要求清空数据库数据、备份的文章和稿件、配置项内容。已核实 `C:\Users\土豆\AppData\Local\WeWriter` 是应用实际数据目录、无 WeWriter 运行进程且目录内无重解析点；删除该目录，覆盖现用/旧 SQLite、自动备份、文章图片、设置、加密密钥与登录状态、日志及浏览器缓存。复查目录、数据库、自动备份、密钥文件均不存在，演示数据目录亦不存在；未创建额外备份。保留仓库源码、测试包及其他位置文件。旧 REQUIREMENTS/PLAN 的 T-007 只读范围与这次直接指令冲突，以人工本轮明确要求为准。下次启动需重配 API Key 与微信读书登录。

T-042 — review — Codex；人工澄清“不出现其他公众号原文标题”的本意是防止成稿点名其他公众号文章，而非禁止正常复用公共政策名称/主题词。此指令覆盖 T-030/T-041 的来源标题连续 10 字一律拦截旧规则。修改 `wewriter/anonymity.py` 与 `tests/test_no_other_accounts.py`：只拦完整/近完整来源标题，或被明确说成文章标题的引用片段；公众号名称及归因说法继续拦截。政策主题重合不再触发额外标题修复请求。离线保密 13、文章质量 31、T040 安全 12、导出 3 项均 OK；交付可靠性 29 项有 1 项旧续跑预期失败（新采集只有 1 篇未用素材，目标 2 篇，现有引擎返回“失败”而测试仍期望“部分完成”），与本次标题检测无关；`git diff --check` 退出码 0（既有换行警告）。独立 `dist/T042/WeWriter-v0.10.4-Windows.zip` 已构建且包内容校验通过，SHA-256 `7B42B16395A392E64DB7F58D9DB84116FD52D64CE5E66A4438D32078467D667D`；未覆盖旧包。真实模型/界面待人工复测，未访问正式数据库写入、真实付费 API、提交或推送。

T-041 — review — Codex；人工以 2026-10-27 13:55 失败截图反馈 T-040 实测并询问原因。只读正式数据库证实：父任务 3 从 55 篇取 1 篇，子任务 4 素材可用 1，写作输出标题连续复用了来源公众号文章标题片段，触发保密校验 `model_output`，未保存稿件；不是采集篇数不足。保留用户“不得暴露其他公众号及原文标题”的铁律。仅改 `wewriter/api.py`、`tests/test_no_other_accounts.py` 与协作文档：仅标题片段重合且正文、大纲合规时先用一次短调用定向修标题；无效结果仍走原整稿重试，最终保密检查照常执行。离线测试：保密 12、文章质量 31、T040 安全 12 项均 OK；包内容检查和 `git diff --check` 通过。独立 `dist/T041/WeWriter-v0.10.4-Windows.zip` SHA-256 `F79E4B2A7D81CBF11FCCD1B2FADA729074E93DD2CD02E77BAE511673625E9EA3`，未覆盖 T040/正式包。真实模型与 GUI 复测待人工；未读取凭据、调用真实 API、修改正式库、提交/推送。

T-040 — review — Codex 主 agent；人工要求分层实施成本、资料复用、成稿可靠性与事实安全重构，由三个子 agent 分别负责架构/技术选型、代码实现、独立代码与功能复核；三线均已完成并交主 agent。文件所有权：架构 agent 仅新建 `docs/collab/T040_ARCHITECTURE.md`；实现 agent 独占本轮业务源码和新/相关离线测试文件，保留全部既有未提交内容；复核 agent 只读；主 agent 独占 `docs/collab/TASKS.md`、`docs/collab/HANDOFF.md` 与独立测试包。已实现可选付费请求次数上限、全入口计量补齐、同任务四阶段断点、跨文章严格摘要复用、事实安全反例修复；独立复核无新阻断 P0。专项测试通过，正式全量/UI shell 未完成。`dist/T040/WeWriter-v0.10.4-Windows.zip` 独立包内容校验通过，SHA-256 `A10C7D4874711342091FB19A68489F4DBE9530D9501621A51234AC2D0D2A0E2E`；打包后 smoke 未取得结果，不宣称通过。等待人工评估测试；人民币硬预算、跨任务政策研究、完整版本/图片备份等需后续单独设计及迁移。旧 REQUIREMENTS/PLAN 顶部 T-007 与本次人工任务不一致，以本轮明确指令为准。未调用真实付费接口、读取凭据、提交/推送或覆盖现有正式包。

T-039 — done — Codex：人工要求全面复审架构、成本、体验、数据复用、鲁棒性与安全性；已完成只读源码审查与离线专项验证。费用 8、后台搜索 12、风格卡 29 项通过（风格卡首跑临时目录数据库错误，改项目内 TEMP 后复跑通过）；虚构域名被当作 official、金额所属对象调换未被 same_facts 拦截均已复现。未调用真实 API、未读取凭据、未修改业务源码、未打包/提交。建议另批成本治理和事实安全修复任务，详细证据见 HANDOFF 与本次回复。

Allowed status: `todo`, `in-progress — Codex`, `in-progress — Claude`, `review`, `blocked`, `done`.

| ID | Status | Owner | Scope/files | Acceptance check | Depends on |
|---|---|---|---|---|---|
| T-038 | review | Codex | 金币弹层图表化：费用总额、分项占比横向柱状图、可展开用量明细与计价依据。只编辑 `wewriter/web/costs.js`、`wewriter/web/styles/pages/costs.css` 和协作记录；独立打包 `dist/T038`，保留 T-036 与正式包 | JS 语法、8 项费用测试、图表渲染 smoke 与 `git diff --check` 通过；EXE 归档含新 JS/CSS，独立 ZIP 校验通过，打包后 demo smoke 六页 ok；ZIP SHA-256 `6850DBB9DF86C0B07BEB6B3A97427176786571174915DFAB9FDFB241ABAB7C9A`。真实 webview 视觉/拖动待人工验收，未调用真实接口、不提交/推送 | T-036 金币组件、T-037 当前工作区 |
| T-037 | review | Codex | 批量目标篇数的付费前检查：采集选文后若候选少于目标，且本任务尚无已保存稿件，停止联网研究和模型写作；明确提示已发生的采集费用。仅改 `wewriter/engine.py`、`tests/test_batch_replacement.py` 和协作记录 | 离线短队列与单篇目标回归均通过；批量替补专项 7 项、独立写作 8 项、热度 2 项、费用 8 项、采集漏斗 17 项、T-010 19 项通过；`git diff --check` 通过。未调用真实 API、未打包/提交/推送；等待人工验证成本与用户体验 | T-036 用户实测反馈 |
| T-036 | review | Codex | 金币用量组件已实现。人工反馈打开旧 T-035 测试包看不到金币；本轮以当前工作区独立打 `dist/T036` 测试包，不覆盖 `dist/T035`、`dist/v0.10.4` 或正式 ZIP。实现文件范围与交接见 HANDOFF.md | T-036 构建退出码 0，包内含 `costs.js` / `costs.css`；标准校验在临时同名目录通过；打包后 demo smoke 退出码 0、6 页 ok；ZIP SHA-256 `ADA4647BA18C9E1A730255B898D293E42D0C0EDAEA3F76CB7AC3642AD2E1DC96`。网页拖动待人工试用，未签名、未提交/推送 | T-035 当前工作区 |
| BUILD-009 | done | Codex | 人工 2026-10-27“打包我测一下”：以当前未提交工作区（含 T-034、T-035 和 Claude 现存改动）生成 `dist/WeWriter-v0.10.4-Windows-T035.zip`；独立 `dist/T035`，未覆盖既有正式包 | PyInstaller 退出码 0；清单 `0.10.4+ga6e0b53.dirty`；ZIP 内容校验通过；打包后 demo smoke 退出码 0、6 页 ok；SHA-256 `B9B261B124FEC57788CC744F82B97C4014A3AF831E400BCB0EDD78228F0A40B9`；未调用真实 API | T-035 当前实现 |
| T-035 | review | Codex 主 agent + 3 子 agent | 人工要求以客户给 83 分的《三篇对比稿-v2》为写法兜底；三路协作已完成设计、局部实现和独立审核。详见 `T035_GOAL.md`、`T035_COLLAB.md`、`T035_PROMPT_DESIGN.md`、`T035_IMPLEMENTATION.md`、`T035_PROMPT_REVIEW.md`；保留现有未提交工作 | 实际发送的提示与每篇写法审核已离线证实；专项 29+14+31 项通过、diff 检查通过；全量测试停滞中断，真实质量未对照；两项事实语义 P1 待处理，不可宣称达到客户 83 分 | T-029/T-034 当前实现 |
| T-034 | review | Codex | 人工反馈 2026-10-27 两轮任务均缺少 `[W]` 引用而失败；仅修 `wewriter/style.py` 联网事实与句末来源标记的绑定、`tests/test_style_card.py` 回归测试，并更新协作文档。保留 Claude 当前未提交工作 | 离线重放失败任务后保留 9/6/8 条 W 事实；专项 24 项通过、diff 检查通过；全量测试中途停滞，已中断，不能宣称通过。隔离新包校验和打包后 smoke 通过；真实模型生成仍待验收 | T-029/T-032 当前实现 |
| T-027 | in-progress — Claude | Claude | 人工 2026-10-27（经 Codex 交接 `WEWRITER-CLAUDE-UI-ROUND1.md`）：五页共享应用外壳 + 工作台第一轮。只改：`wewriter/web/common.js`、`workbench.html`、`workbench.js`；`accounts.html`/`articles.html`/`drafts.html`/`settings.html` 仅为接入共享外壳的标记与样式（不改四页业务 JS）；**补登记（Claude 2026-10-26 据 diff）：实际还改了 `wewriter/web/articles.js`（书架式文章列表与“抽出”交互），新增 `styles/pages/{accounts,articles,drafts,settings}.css`；`tests/test_workbench_ui.py` 未见**；新增 `wewriter/web/styles/{tokens,base,components}.css`、`styles/pages/workbench.css`、`wewriter/web/assets/cat/*.png`（复制现有 5 张猫图）、`tests/test_ui_shell.py`、`tests/test_workbench_ui.py`；协作文档。基线：HEAD a6e0b53，工作区仅含 Claude 既有 T-013、T-017～T-026 未提交改动（保留；workbench.js 内含 T-026 历史行文案，drafts.html 含 T-017 样式，settings.html 含 T-023 开关） | 五页侧栏一致、仅浅/深两档；工作台九个 wb_* 桥接全部可用；离线测试、JS 语法、全量回归、git diff --check；pywebview 替身数据观察。不打包、不调用真实采集/模型 | T-026 |
| T-031 | review | Claude | 人工 2026-10-26：排查改用 DeepSeek 后二审失败。根因：稿件 13（deepseek-v4-pro）任务与两次补跑共 3 次均为“二审未返回有效的逐句核对”——逐句核对任一条格式不合规即整次二审失败且不重写。修复（不放松事实核对）：verdict 去掉括号说明后识别、保留项 note 缺失或 null 视为空、删除/新增项不适用的句子 null 视为空；无效条目丢弃（改动句仍须全部列入核对）；全部无效时按 validation 带具体原因定向重写一次；提示词补格式约束；日志记录无效字段。编辑：`wewriter/api.py`、`tests/test_article_quality.py`、协作文档 | 全量测试；真实 DeepSeek 二审需人工验证 | T-030 |
| BUILD-008 | done | Claude | 协调者转述人工决策卡（程序编号方案，含重跑与打包）：对比三篇成功后打包替换 BUILD-007。含 T-032、T-033 及对比后发现的 [F#] 残留修复（未在事实清单上的 F 编号直接去掉、重复来源标记合并，写作/二审/定向修改/成稿校验四处都处理）；构建前无 WeWriter.exe 进程，BUILD-007 zip（SHA256 1955CC56…）备份为 `.local-data/package-backup/WeWriter-v0.10.4-Windows-BUILD-007.zip`；`scripts/build.ps1` 不签名，未改版本号 | 构建退出码 0、包校验通过、打包后 smoke ok、全量 437 OK | T-033 |
| T-033 | review | Claude | 人工 2026-10-27 决策卡选“程序编号，模型只答依据”（协调者转述）：二审不再让模型抄写逐句核对表。第 1 次调用只交修订稿；程序对比初稿和修订稿，把改动编号为 C1…（标题改动排第一）、删除编号为 D1…；第 2 次小调用只按编号回答依据（F 编号或来源编号，无事实的句子可答“无需依据”，含数字、引语或来源标记的不行）；漏答、无效编号、无依据、说法强度超过可信等级的句子做 1 次定向修改，仍不合格的回退为初稿原句（初稿无对应句则删除），不再整篇弃用；对照表由程序生成；补跑二审同样适用；check_problems / claim_check 保留用于旧报告与查证修订。调用次数：无改动 1 次，正常 2 次，有问题句 3 次（引用校验重试另加 1 次）。编辑：`wewriter/api.py`、`tests/test_article_quality.py`、`tests/test_claim_checks.py`、`tests/test_readability.py`、`tests/test_style_card.py`、协作文档 | 全量 436 OK、git diff --check 通过；稿件 10 两份被拒修订稿离线回放分别编号 4 改 1 删、4 改 0 删 | T-032 |
| T-032 | review | Claude | 人工 2026-10-27“同意跑对比”：T-029 对比在稿件 10 写作即失败（两次写作均“正文缺少来源标记或包含无效来源”）。推测原因：事实表每条带 F 序号，模型把 [F#] 当来源写进正文。修复：事实表每条加 `cite`（如 [S1][W2]）、提示词要求原样写 cite 且 F 序号不能写进正文；写作返回后把 [F#] 映射回该事实的来源标记再校验；来源校验失败时报错列出无效编号。2026-10-27 协调者追加：风格卡生成后用范文“不要学”、taboos 和 bad 示例检查正面举例，命中即删（`style.clean_style_card`），生成提示词同步写明；对比脚本加 `--card`（复用已存风格卡并清理，不调用模型）和失败即停、原始输出存 `draft-N-failed.json`。编辑：`wewriter/style.py`、`wewriter/api.py`、`scripts/compare_readability.py`、`tests/test_style_card.py`、协作文档 | 全量 437 测试 OK、git diff --check 通过；真实重跑对比需人工批准费用 | T-029 |
| BUILD-007 | done | Claude | 人工 2026-10-26“改完打个新包”：含进行中的 T-027 界面改动、T-029、T-030、T-031 的当前源码（未提交，dirty）；`scripts/build.ps1` 不签名；构建前 dist 中的 zip（03:13 生成、76,987,996 字节、未在 TASKS 登记，推测为他人重建）先备份为 `.local-data/package-backup/WeWriter-v0.10.4-Windows-before-BUILD-007-(03-13-unregistered).zip` | 构建退出码 0、包校验、打包后 smoke | T-031 |
| T-030 | review | Claude | 人工 2026-10-26：①铁律：稿件不得出现任何其他公众号（名称、作者、原文标题、“公众号/推文/原文作者/原文称/据某号”等说法）；写作、事实审核、风格审核、查证修订提示加铁律；程序黑名单（全部公众号名称、来源公众号名、微信来源文章标题及其 ≥10 字片段、通用说法）硬校验：写作命中退回重写一次，仍命中由程序改为中性说法或删句；审核命中弃用修改稿；保存前最终扫描；导出与复制全文不列微信链接，命中则阻止；旧稿打开时红色提示。②工作台历史提示“合格”改为“已审核通过”；补记 T-027。编辑：新 `wewriter/anonymity.py`、`wewriter/api.py`、`wewriter/style.py`（事实清单出处不写公众号名）、`wewriter/pipeline/composition.py`（leak_guard）、`wewriter/ui.py`（导出过滤）、`wewriter/webapp.py`（扫描/阻止导出/dr_get leaks）、`wewriter/web/drafts.js`（红色提示）、`wewriter/web/workbench.html`（一句提示语）、`tests/test_no_other_accounts.py`（新）、`tests/test_delivery_reliability.py`/`test_export.py`/`test_t010.py`/`test_style_card.py`（随规则调整断言）、协作文档 | 离线测试、全量测试 | T-029 |
| T-029 | review | Claude | 人工 2026-10-26：“问题在于模型怎么理解客户需要的风格，然后能保证风格又保证真实性”。写作流程重构为“事实清单 + 风格卡/范文 + 两道审核”：程序从摘要/引语/联网研究组装事实清单（可信等级、适用范围、允许说法强度）；范文库（DB v6）与风格卡（一次性模型调用生成，存配置，可查看修改）；写作提示只给风格卡、1～2 篇范文、事实清单；事实审核加说法强度检查，风格审核按维度打分并只改写法、事实集合不变才采用；T-028 数字阈值降为风格审核参考信号；稿件“设为范文/反例”；生动与编造对照示例。编辑：`wewriter/api.py`、新 `wewriter/style.py`、`wewriter/db.py`（v6）、`wewriter/config.py`、`wewriter/pipeline/composition.py`、`wewriter/webapp.py`、`wewriter/web/settings.js`（动态注入范文库区块，不改 settings.html，避开 T-027）、`wewriter/web/drafts.js`（设为范文/反例按钮）、`scripts/compare_readability.py`、测试与夹具、协作文档 | 离线测试、全量测试；风格卡生成与对比需授权费用 | T-028 |
| T-028 | review | Claude | 人工 2026-10-26：稿件太拗口、模板化（原登记为 T-027，与并行的界面任务 T-027 重号，改为 T-028）。标题≤22 字带具体钩子、不含含糊词，开头 2～3 句讲清事件和与读者的关系，来源说明不进标题和首段；口语化短句与禁用词；5 种结构原型由写作模型选择并说明理由，近期稿件标题/开头/小标题作为避免雷同输入；二审加可读性检查，程序检查句长、禁用词、标题、与近期稿件相似度，超阈值复用定向重写一次（仍须过逐句核对）；风格预设与迁移。编辑：`wewriter/api.py`、`wewriter/pipeline/composition.py`、`wewriter/config.py`、`wewriter/ui.py`（STYLE_PRESETS）、`wewriter/web/settings.js`、`scripts/compare_readability.py`（新，不运行）、`tests/test_readability.py`（新）、`tests/test_article_quality.py`（一处断言）、协作文档 | 离线测试、全量测试；真实前后对比需授权费用 | T-026 |
| BUILD-006 | done | Claude | 人工 2026-10-26“打包”：含 T-025、T-026 的当前源码（未提交，dirty）重新打包；`scripts/build.ps1` 不签名；覆盖 `dist/v0.10.4` 与 zip，BUILD-005 的 zip 先备份到 `.local-data/package-backup/WeWriter-v0.10.4-Windows-BUILD-005.zip`；只更新协作文档 | 构建退出码 0、包校验、打包后 smoke | T-026 |
| T-026 | review | Claude | 人工 2026-10-26“改一下”：工作台历史“合格稿件 X/Y”改为“已生成 N/目标 · 已审核通过 M”（批量汇总子任务）；因素材不足的“部分完成”显示为“素材不足，完成 N/目标”，其他部分完成显示“部分完成 N/目标”。只改显示，不改状态和数据库；当前任务、任务详情经同一 `job_status_label` 保持一致。编辑：`wewriter/ui.py`、`wewriter/webapp.py`（wb_history）、`wewriter/web/workbench.js`、`tests/test_status_labels.py`（新）、协作文档 | 全量测试、git diff --check | T-025 |
| T-025 | review | Claude | 人工 2026-10-26：点“补跑二审”前端没反应。按钮点击即显示“正在启动…”；启动被拒、运行失败、完成都写在稿件页审核行（不只 3 秒提示）；刷新失败不再让页面停在“补跑中…”；上一个任务仍在停止时给出明确提示；补跑开始/完成/失败写入日志。编辑：`wewriter/web/drafts.js`、`wewriter/webapp.py`（dr_rereview）、`tests/test_drafts_page.py`（新，Playwright 真浏览器）、`tests/test_rerun_review.py`、协作文档 | 全量 385 项、git diff --check；需重新打包后人工点击确认 | BUILD-005 |
| BUILD-005 | done | Claude | 人工 2026-10-26“打包”：含 T-023、T-024 的当前源码（未提交，dirty）重新打包；`scripts/build.ps1` 不签名；覆盖 `dist/v0.10.4` 与 zip，BUILD-004 的 zip 先备份到 `.local-data/package-backup/WeWriter-v0.10.4-Windows-BUILD-004.zip`；只更新协作文档 | 构建退出码 0、包校验、打包后 smoke | T-024 |
| T-024 | review | Claude | 人工 2026-10-26“按这个改”：查证前移到写作前的联网研究，减少第二次搜索。程序从原文摘要事实中挑出含关键标识（金额、项目编号、文号、中标单位等）的说法，作为 key_facts 交给第一次联网研究，要求同时查找其官方原件并引用；二审后的自动查证保留为兜底（只在仍有待查证事项时触发）。编辑：`wewriter/research.py`、`wewriter/pipeline/research.py`、`wewriter/api.py`（标识抽取与覆盖判断）、`wewriter/pipeline/composition.py`（兜底过滤、素材报告）、`tests/test_key_facts.py`（新）、协作文档 | 离线测试、全量测试；真实搜索效果需人工 | T-023 |
| T-023 | review | Claude | 人工 2026-10-26：二审不能把查证推给用户。写作/二审提示规定存疑事实三选一（有源标引用 / 无源改审慎表述或删除 / 列 verify_items 交程序）；程序把 review_notes 中的查证待办转成 verify_items，不展示；二审后按开关“二审后自动查证存疑事实”（默认开）每篇最多一次定向联网搜索（复用后台模式与引用校验）+ 一次定向修订（须过 T-022 逐句核对）；查不到改审慎表述，联网失败静默跳过只记素材报告；审核提示语义去重；修复引号引用正文挂不上段落。编辑：`wewriter/api.py`、`wewriter/research.py`、`wewriter/pipeline/composition.py`、`wewriter/config.py`（auto_verify 默认 true）、`wewriter/ui.py`（review_marks）、`wewriter/webapp.py`、`wewriter/web/settings.*`、`wewriter/web/drafts.js`、`wewriter/db.py`（revise_draft 可存新来源）、`tests/test_verify_items.py`（新）、协作文档；补充：补跑二审也自动查证、未查证数量提示 | 离线测试、全量测试；遵义 id 11 与马鞍山 id 12 离线回放；真实查证需人工 | T-022 |
| BUILD-004 | done | Claude | 人工 2026-10-26“打包”：含 T-022 的当前源码（未提交，dirty）重新打包；`scripts/build.ps1` 不签名；覆盖 `dist/v0.10.4` 与 zip，BUILD-003 的 zip 先备份到 `.local-data/package-backup/WeWriter-v0.10.4-Windows-BUILD-003.zip`；只更新协作文档 | 构建退出码 0、包校验、打包后 smoke | T-022 |
| T-022 | review | Claude | 人工 2026-10-26“改”：二审提示词与程序复查标准统一（底层修复，不做显示层处理）。核对清单改为 draft_sentence/final_sentence/evidence/verdict（含“新增”），兼容旧格式；程序逐句比对初稿与修订稿算出改动集，硬校验覆盖、保留一致、删除不在正文、final_sentence 在正文、改动标题已列入；不通过定向重写一次，仍不通过弃用修改稿、二审记失败；素材报告两栏表；不带入已删除引语的旧提示；“当作/不应……传播”不算传播断言；补跑二审开放给已通过二审的稿件。编辑：`wewriter/api.py`、`wewriter/pipeline/composition.py`、`wewriter/ui.py`（review_marks 读表格）、`wewriter/web/drafts.js`、`tests/test_claim_checks.py`（新）、`tests/test_article_quality.py`、`tests/test_rerun_review.py`、协作文档 | 离线测试 + 全量 353 项；稿件 id 11 离线回放；真实模型二审需人工验证 | T-021 |
| BUILD-003 | done | Claude | 人工 2026-10-26“打包”：用 `scriptsbuild.ps1` 打包当前源码（含 T-017～T-021 未提交改动，dirty 构建）；覆盖 `dist/v0.10.4` 与 zip，旧 zip 备份到 `.local-datapackage-backup`；只更新协作文档 | 构建退出码 0、包校验通过、打包后 smoke | T-018～T-021 |
| T-018 | review | Claude | 人工 2026-10-26（经协调会话转达）：写作与二审改为流式输出，空闲超时 60 秒 + 总时长 15 分钟，读超时不重发，保留非流式回退（服务商配置 `stream: false`，或服务端不支持流式时自动按普通 JSON 处理）。编辑：`wewriter/api.py`、`tests/test_streaming.py`、协作文档 | 离线：chat/completions 与 Responses 的 SSE 拼接、空闲超时、总时长、截断、错误对象、回退；全量测试。真实 DeepSeek/OpenAI 流式需用户用真实 Key 验证 | BUILD-002 已于 12:30 出包，无打包进程 |
| T-019 | review | Claude | 联网搜索改 OpenAI 后台模式（background + store:true），response id 先落库，5 秒轮询，10 分钟上限后 cancel 并降级；重试复用未取回的 id；可被停止任务取消。编辑：`wewriter/research.py`、`wewriter/pipeline/research.py`、`wewriter/db.py`（v5 迁移：research_pending 表）、`tests/test_background_research.py`、`tests/test_db_migrations.py`/`tests/test_delivery_reliability.py`（仅版本断言 4→5）、协作文档 | 离线：轮询成功、轮询网络错误后恢复、超时取消并降级、重试复用 id、停止任务；迁移幂等；全量测试。真实后台模式需用户验证 | T-018 |
| T-020 | review | Claude | 批量补位扩展到单篇网络/超时失败（每批最多 2 篇），Key/余额/权限/参数错误不补位；说明文字写明“因超时失败，已由热度第 N 篇补位”。编辑：`wewriter/engine.py`（run_separate）、`tests/test_batch_replacement.py`、协作文档 | 离线：超时可补、Key 错误不补、最多 2 篇、说明文字；全量测试 | T-019 |
| T-021 | review | Claude | 稿件页“补跑二审”：二审未完成/失败的稿件可只调用一次 writer.review，通过校验后保存为新版本（旧版本保留），失败不改稿件，缺资料明确提示，运行中禁止重复并与任务互斥。编辑：`wewriter/pipeline/composition.py`（rerun_review）、`wewriter/engine.py`（入口）、`wewriter/db.py`（revise_draft）、`wewriter/webapp.py`（dr_get 返回二审状态、dr_rereview）、`wewriter/web/drafts.*`、`tests/test_rerun_review.py`、协作文档。保留 T-017 在这些文件中的未提交改动 | 离线：成功、失败、缺资料、重复点击/任务互斥；全量测试。真实页面点击需人工 | T-020 |
| T-017 | review | Claude | 人工 2026-10-26：有“事实或质量提示待核实”的稿件，在稿件页把需核实的段落直接标红。编辑：`wewriter/ui.py`（review_marks）、`wewriter/webapp.py`（dr_get 返回 marks/unplaced）、`wewriter/web/drafts.*`、`tests/test_review_marks.py`、协作文档 | 新测试 5 项 + Windows 全量测试；真实稿件观察待人工 | T-014 |
| T-016 | done | Claude | 人工 2026-10-26 改由 Claude 执行。`engine.py` 第一阶段模块化（纯搬迁，不改业务逻辑）。可编辑：`wewriter/engine.py`、新包 `wewriter/pipeline/`（`__init__.py`、`common.py`、`collect.py`、`materials.py`、`research.py`、`composition.py`）、协作文档；不改测试、不改 T-017 文件 | 行为不变：基线与拆分后全量 `unittest discover -s tests` 均 299 项 OK；搬迁函数 AST 逐一比对一致；`import wewriter.engine, wewriter.ui, wewriter.webapp` 通过；`verify_delivery.py` 6 项通过；`git diff --check` 通过。人工 2026-10-26 告知 Codex 审查完成；已合并 main（PR #1，合并提交 10a710f）；人工 2026-10-26 确认验收通过 | T-013 的 engine 改动已在 74c5ccd 提交，工作区 engine.py 拆分前与 HEAD 一致 |
| T-015 | done | Codex | 招聘者版 README、Mermaid 架构图、免 Key Demo GIF、GitHub Actions；`README.md`、`.github/workflows/test.yml`、`docs/showcase/`、`scripts/build_demo_gif.py`、协作文档 | 顶部 30 秒可理解；长文折叠；CI 在 push/PR 跑全量 unittest；Demo 不含真实客户数据；链接与测试通过 | `engine.py` 拆分转 T-016，等待 T-013 结束 |
| T-014 | done | Claude | 人工 2026-10-26：稿件库“复制全文”（不导出文件直接复制）；客户 Windows 11 智能应用控制拦截的原因分析与签名准备。编辑：`wewriter/ui.py`（clipboard_text）、`wewriter/windows.py`（剪贴板）、`wewriter/webapp.py`（dr_copy）、`wewriter/web/drafts.*`、`tests/test_copy.py`、`WeWriter.spec`（关 UPX、版本信息、可选内置二进制签名）、`scripts/build.ps1`（-Sign）、`scripts/build_to_desktop.cmd`、新 `scripts/sign.ps1`、`docs/代码签名与智能应用控制.md`、`README.md`（两句说明）、协作文档 | Windows 全量 294 项通过；无证书构建及包校验通过；真实签名与真实剪贴板粘贴仍需环境验收 | 与 BUILD-002 相邻：改了构建脚本，Codex 已审查并修正签名目标 |
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
