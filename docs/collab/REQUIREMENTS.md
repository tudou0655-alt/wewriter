# Requirements — 公众号信息获取及文章生成-源码-v0.10.3

Status: approved for T-001
Owner: human
Last updated: 2026-10-16

## Problem

当前诊断日志仅通过有限前缀的正则表达式隐藏敏感信息，缺少独立测试来证明不同日志形态中的虚构 API Key、Authorization 值和微信读书 Cookie 都不会被写入日志。需要在不接触真实凭据、不改变业务流程的前提下，加固日志脱敏并建立可重复的测试证据。

## User outcomes

- 诊断日志保留排错所需的错误类型、HTTP 状态、请求 ID、文件名和普通上下文。
- 明显属于 API Key、Bearer 凭据、Skill Key 或微信读书 Cookie 的值不会以可还原形式出现在日志中。
- 后续凭据格式扩展可通过集中、可测试的脱敏逻辑维护。

## In scope

- T-001：日志敏感信息脱敏加固。
- 实现者：Claude。
- Claude 唯一允许编辑的四个文件：`wewriter/logs.py`（程序实现）、`tests/test_logs.py`（测试）、`docs/collab/TASKS.md`（仅更新 T-001 状态和验收勾选）、`docs/collab/HANDOFF.md`（仅记录实现结果、验证证据和交接）。
- Codex 在实现完成后进行只读审查，不修改任何文件。
- 测试数据只能使用明显虚构的密钥和 Cookie。

## Out of scope

- 不读取、复制、记录或要求任何真实 API Key、Cookie、微信读书登录状态或其他凭据。
- 不调用真实 API，不访问外部服务，不安装或升级依赖。
- 不修改日志目录、轮转策略、业务流程、网络请求、密钥存储或 UI。
- 除上述四个文件外不得编辑任何文件；对两份协作文档的编辑不得超出规定的任务追踪用途。
- 不提交、不推送、不创建或删除分支，不改变远程状态。

## Functional requirements

| ID | Requirement | Priority | Acceptance evidence |
|---|---|---|---|
| R-001 | 脱敏 Authorization Bearer 值及常见 `sk-` 形式的虚构密钥 | Must | `tests/test_logs.py` 的表驱动断言 |
| R-002 | 脱敏 URL、JSON 或键值文本中的虚构 API Key / Skill Key 值 | Must | 测试证明原值不在过滤后的日志消息中 |
| R-003 | 脱敏微信读书 Cookie 中的敏感值 | Must | 覆盖 `wr_skey`、`wr_rt`、`wr_vid` 等虚构 Cookie |
| R-004 | 保留 HTTP 状态、请求 ID、错误类型、文件名及普通文本 | Must | 测试验证非敏感诊断信息未被删除 |
| R-005 | 日志参数化消息和异常消息经过脱敏时不得导致日志写入失败 | Must | 单元测试覆盖参数化记录和异常文本 |
| R-006 | 避免把普通、非凭据文本过度脱敏 | Must | 至少一个近似但非凭据的反例测试 |

## Non-functional requirements

- Security: 所有测试凭据必须明显虚构；禁止读取真实凭据文件或环境变量中的凭据。
- Reliability: 脱敏逻辑遇到意外输入时不得使日志记录失败。
- Maintainability: 敏感模式应集中定义，测试用例应便于新增格式。
- Compatibility: 保持 Python 3.13.15 和现有 Windows 日志行为兼容。
- Performance: 脱敏不得引入网络、文件扫描或明显的每条日志高开销处理。

## Acceptance baseline

- Python：3.13.15。
- 基线测试：111 项 `unittest` 通过。
- 基线提交：`d8f92fa`。
- 基线验证命令：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。

## Human approval gates

- 本文档仅批准 T-001 及上述四个获准文件，并分别遵守其限定用途。
- 任何新增文件、扩大脱敏范围之外的行为变化、依赖变化或真实凭据访问都必须停止并单独请求人工批准。
- 提交、推送、合并、发布和部署均需人工另行批准。
