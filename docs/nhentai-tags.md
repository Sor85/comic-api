# JM → nhentai 标签映射

nhentai 标签搜索接受 JM 主题标签名称，并将已登记的名称转换为对应的英文标签。映射仅在 nhentai 的标签模式中生效，适用于单源搜索和聚合搜索；JM、哔咔、拷贝接收到的搜索词不变，普通关键词搜索不转换。

## 范围

- 内置 24 组对应关系，共 35 个名称，包含表中明确列出的繁简别名；英文缩写匹配不区分大小写。
- 只匹配完整名称，不做分词、子串替换或自动翻译。未登记的名称仍按原文搜索，不保证有结果。
- 详情标签携带的原生检索值优先；显式输入的 `tag:"名称"`、`artist:"名称"`、`language:"名称"` 等原生查询保持不变。
- 只改写搜索请求，不改写 nhentai 详情中的标签名称、类型或检索值，不反向修改 JM 标签。
- 映射本地加载，不增加远程翻译、标签查询或 JM 请求，也不要求开启 JM 或绑定账号。
- 只表示标签含义对应，不保证两站收录作品、标注规则或结果数量一致。

## 映射表

目标类型均为 `tag`，请求格式为 `tag:"英文标签"`。

| JM 名称及支持的别名 | nhentai 标签 |
| --- | --- |
| 女性向 | `josei` |
| 全彩 | `full color` |
| 近親、近亲 | `incest` |
| 百合 | `yuri` |
| YAOI | `yaoi` |
| 性轉、性转 | `gender bender` |
| NTR | `netorare` |
| 巨乳 | `big breasts` |
| 貧乳、贫乳 | `small breasts` |
| 教師、教师 | `teacher` |
| 女僕、女仆 | `maid` |
| 護士、护士 | `nurse` |
| 眼鏡、眼镜 | `glasses` |
| 連褲襪、连裤袜 | `pantyhose` |
| 兔女郎 | `bunny girl` |
| 群交 | `group` |
| 足交 | `footjob` |
| SM | `bdsm` |
| 肛交 | `anal` |
| 阿黑顏、阿黑颜 | `ahegao` |
| 藥物、药物 | `drugs` |
| 扶他 | `futanari` |
| 自慰 | `masturbation` |
| 觸手、触手 | `tentacles` |

## 来源与维护

核对日期：2026-10-19。

- JM 名称取自匿名 `GET /categories` 返回的 `blocks[].content` 主题标签目录；繁简别名为人工登记，不依赖运行时繁简转换库。
- nhentai 名称、类型及存在性通过[官方 API v2 文档](https://nhentai.net/api/v2/docs)中的 `GET /tags/tag` 与 `POST /tags/search` 核对。
- 表为人工整理的标签对应关系，未引入第三方翻译词库；实现位于 `src/sources/_nhentai_tags.py`。
- 目录中没有已确认对应项、名称范围不一致或无法确定等价关系的标签暂不收录。例如「人妻」不能直接等同于 `milf`，「催眠」不自动扩大为 `mind control`，「其他制服」不推断为某一种具体制服。
- 增补映射时须同时核实 JM 标签及 nhentai 的真实名称、类型和含义，并补充繁简别名及回归测试；不能仅凭直译生成目标标签。
