# T-047 基础费率与计费口径（2026-10-27）

以下是官方公开标价，不含优惠、税费、资金抵扣或实时汇率。程序仅对已识别的官方接口和模型估算；未知模型、接口或未取得用量的请求标为待核对。历史事件保留当时写入的费率，不按新价重算。未进行真实付费调用。

| 服务与模型 | 输入未命中/百万 token | 输入缓存命中/百万 token | 输出/百万 token | 搜索工具 |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek `deepseek-flash` 峰时 | ¥2 | ¥0.04 | ¥8 | 原生 Search 成功 ¥0.005/次 |
| DeepSeek `deepseek-flash` 谷时 | ¥1 | ¥0.02 | ¥4 | 同上 |
| DeepSeek `deepseek-v4-pro` 峰时 | ¥9 | ¥0.30 | ¥27 | 原生 Search 成功 ¥0.005/次 |
| DeepSeek `deepseek-v4-pro` 谷时 | ¥4.5 | ¥0.15 | ¥13.5 | 同上 |
| 百炼北京 `qwen3.8-flash` | ¥0.8 | ¥0.1 | ¥2.7 | Responses agent 搜索 ¥0.004/次 |
| OpenAI `gpt-5-mini` | $0.25 | $0.025 | $2 | web_search $0.01/次 |
| OpenAI `gpt-5.6-luna` | $0.20 | $0.02 | $1.20 | 同上 |
| OpenAI `gpt-5.6-terra` | $2 | $0.20 | $12 | 同上 |
| OpenAI `gpt-6-luna` | $0.10 | $0.01 | $0.50 | 同上 |

DeepSeek 峰时为北京时间周一至周五（中国法定节假日除外）9:00–12:00、14:00–18:00；其余为谷时。程序已收录国务院公布的 2026 年假期区间。未来年份假期尚未录入前，峰谷归类仅按普通工作日估算。OpenAI 人民币展示固定按 1 USD = ¥6.70 换算，属示意汇率。百炼只为北京地域 `qwen3.8-flash` 报价；其他地域、模型、专属协议或自定义地址计价需另核。

DeepSeek 原生 Search 接口只在 HTTP 200 且 `results` 非空时收费；空结果计 0。百炼 Responses 搜索次数优先读取 `usage.x_tools.web_search.count`；OpenAI 按完成的 `web_search_call` 计数。模型费用采用提供方返回的总输入、缓存命中、输出 token。无法取得这些字段时不把费用记为零。显示值不是供应商账单，实际扣费可能受优惠、峰谷、汇率、长上下文、缓存写入等影响。

2026-10-28 修正：OpenAI GPT-5.6 及后续模型的 Responses 缓存写入数量在 `usage.input_tokens_details.cache_write_tokens_details` 下，分别按 30 分钟 1.25 倍、12 小时 2.5 倍输入费率计价。本程序未指定缓存有效期时采用官方默认 30 分钟。此前错误地从上一层读取明细，导致含缓存写入的 Luna 请求显示“待核对”；旧计费事件未保存写入 token 数，不能精确补算。

官方来源：

- [DeepSeek 模型与峰谷价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [DeepSeek Search 接口计费](https://api-docs.deepseek.com/zh-cn/guides/search/)
- [国务院 2026 年节假日安排](https://www.gov.cn/zhengce/content/202511/content_7047090.htm)
- [百炼 Qwen3.8-Flash 价格](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)
- [百炼联网搜索计费](https://help.aliyun.com/zh/model-studio/web-search/)
- [OpenAI 模型与工具价格](https://developers.openai.com/api/docs/pricing)
- [OpenAI 提示缓存用量字段与计价](https://developers.openai.com/api/docs/guides/prompt-caching)
