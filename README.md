# Comic API

Python / FastAPI 多图源漫画 WebUI。内置禁漫、哔咔、拷贝漫画；前端按插件能力生成浏览入口与登录表单。

右上角「图源管理」显示图源列表，每行进入独立「设置」。有登录能力的图源提供「登录凭证」子页面，再填写账号与密码；拷贝漫画提供服务器地址设置，保存到 SQL，优先于环境变量，新请求立即生效。正在执行的请求继续使用原地址；留空可恢复部署默认地址。服务器地址仅接受不含嵌入凭据的 HTTPS URL，只应填写可信服务器。

## 部署

```bash
cp .env.example .env
docker compose up -d --build
```

访问 http://localhost:34587 。哔咔和禁漫账号可在「图源管理 → 设置 → 登录凭证」绑定；哔咔也可在首次启动前填入 `.env`。拷贝使用游客接口。禁漫收藏夹与自动同步需要账号，登录兼容 JWT 和 AVS Cookie 会话，凭据与会话均加密保存在 SQL。

本地运行：

```bash
pip install -r requirements.txt
python main.py
```

本地端口 8699。只运行一个服务进程 / 一个 Uvicorn worker：下载队列与图源限流器在进程内管理，不支持多个实例共享数据库。当前没有站点用户认证；图源账号、书库与下载属于服务实例，**不要直接暴露到公网**，需要时在反向代理增加访问认证。

## 数据与功能

- **混合搜索**：各图源结果统一展示并标注来源，按标题完全匹配、包含关键词、字符相似度排序；忽略大小写、全半角与标点差异。同名的不同图源版本保留独立入口。排序范围是本次返回的结果（聚合搜索为各源首页），不是全站全部漫画，也不包含语义理解或简繁转换。

- **图片代理**：封面与章节图片统一经 `/api/image/proxy` 中转，浏览器不直接连图源 CDN；代理拒绝非公网地址（SSRF 防护），成功响应按源+URL 缓存到磁盘（最多保留约 2000 张）。
- **图源账号**：SQLite 按图源隔离，账号与 Token 使用 AES-GCM 加密。首次启动自动导入原 `.bika_token` / `.bika_credentials.json`，之后以 SQL 为准；退出登录不会重新导入。旧文件不会自动删除，确认迁移成功后可自行清理其中的明文凭据。
- **书库**：收藏、分类、移除、缓存详情与手动检查章节更新；页面支持按分类与标题/作者关键词筛选。同一本漫画的不同图源记录互不覆盖。尚未实现后台定时追更。
- **下载任务**：排队、图片进度、打包状态、取消、失败重试、取回 PDF、删除任务及文件；详情页可一键把全部章节加入队列（已有同章节任务自动去重）。单任务执行，最多 100 个排队任务。重启后未结束的任务标记为中断，需手动重试；重试从头开始，不是断点续传。取消会等待已开始的网络请求或打包操作退出并清理文件。
- **阅读进度**：仅保存在浏览器 localStorage，按图源、漫画和章节隔离；支持续读、滚动/翻页模式。不会写入服务器 SQL，也不跨浏览器同步。清除站点数据或更换访问域名/端口会影响可见进度。
- **明确错误**：接口区分登录失效、限流、网络超时、上游错误与参数错误。聚合搜索保留可用图源结果，并单独报告失败源，不把失败伪装成空结果。
- **并发边界**：每源最多 2 个业务调用；每客户端最多 4 个网络请求；阅读图片最多 8 个并发处理；PDF 默认最多 4 个图片下载。同步请求在线程执行，取消等待不会提前释放仍在工作的线程配额。拷贝章节请求间隔至少 6 秒，并缓存成功结果 10 分钟。

默认数据目录为 `./data`，可用 `DATA_DIR` 修改。Docker 已挂载 `./data:/app/data`：

| 路径 | 内容 |
| --- | --- |
| `comic.sqlite` | 图源凭据密文、书库、下载记录 |
| `credentials.key` | 凭据加密密钥，必须保密并与数据库一起备份 |
| `downloads/` | 已生成的 PDF |
| `jm-favorites/` | 默认 JM 收藏图片同步目录（可在允许的根目录内修改） |
| `plugins/` | 自定义可信 Python 图源 |

备份建议先停止服务再复制整个数据目录，避免遗漏 SQLite WAL。密钥丢失后无法解密旧账号。加密不防御可同时读取数据库和密钥的主机管理员。PDF 密码显示在任务卡上，沿用确定性六位密码，主要用于阅读流程而非强机密保护。

## JM 收藏与图片同步

导航「JM 收藏」（或 `/?tab=jm-favorites`）读取已绑定账号的云端收藏，支持自建收藏夹、分页、按收藏时间或漫画更新时间排序、本页标题/作者筛选及多选。`0` 是全部收藏，包含未放入自建目录的漫画。收藏页与本地「我的书库」是独立概念，不会修改远端收藏。

### 同步行为

- 「同步收藏」按设置中的范围扫描全部分页；「同步本书」「同步所选」直接同步指定漫画，不受收藏夹范围限制。
- 定时同步默认关闭。保存并启用后立即执行，之后以每轮结束时间加间隔计算下一轮（5 分钟到 7 天）。只在服务运行时执行，浏览器可以关闭；服务重启后按保存的计划继续。
- 单个同步 worker，漫画、章节和图片依次处理。已有任务运行时拒绝重入及设置/账号变更；需要先停止并等待结束。更换或解除禁漫账号绑定后关闭定时同步，避免用新账号意外继续旧计划。
- 全部收藏逐页扫描，按漫画 ID 去重，再获取最新详情与章节，因而会补充新增章节。发现重复页、总数变化或不完整响应时明确报错，不把失败当作空收藏；远端持续变化时可稍后重试。
- 文件清单持久化到 SQLite，按输出配置、漫画、章节和页码记录 URL 路径、文件位置、大小与 SHA-256。存在且校验一致的图片跳过，缺失或损坏的已记录图片重新下载；失败项在下一轮重试，不重复下载已完成项。
- 停止与重启保留已完成图片；中止当前请求后结束。没有字节级断点续传，未完成的单张图片会重新下载。任务记录保留最近 30 次，页面显示最近 10 次，每轮保留前 100 条失败明细。
- 可同步添加到本地书库，已有分类不变，新漫画使用「JM 收藏」分类。远端取消收藏、删夹或清空后不会删除本地书库和文件。

### 下载设置

| 设置 | 行为 |
| --- | --- |
| 收藏夹范围 | 未勾选为全部收藏，可选择多个自建收藏夹；删除的目录不会静默回退到全部 |
| 下载限速 | 单位 KiB/s，`0` 不限速；使用 libcurl 接收限速并限制短请求平均速率，仅作用于本功能的图片下载，不限制在线阅读、API 元数据或现有 PDF 任务 |
| 图片格式 | 原格式 / JPEG / PNG / WebP；先 JM 反切割，再真实编码，输出扩展名匹配编码格式 |
| 图片质量 | 1–100，仅 JPEG / WebP 生效；JPEG 的透明区域铺白底；PNG 无损，不使用质量参数 |
| 动图 | 原格式保留 GIF 动画并跳过反切割；主动转换成 JPEG/PNG/WebP 时仅保留首帧 |
| 重试 | 每张图片额外重试 0–5 次，带退避；单张图片输入上限 64 MiB，下载不跟随重定向 |
| 目录规则 | 漫画 ID/章节 ID；漫画标题 [ID]/章节序号 - 标题 [ID]；作者/漫画标题 [ID]/章节序号 - 标题 [ID] |
| 文件名 | 五位页码，如 `00001.jpg`；目录名称清理 Windows 不支持的字符及保留名称，编号用于区分同名漫画/章节 |

默认保存到 `DATA_DIR/jm-favorites`。目录与规则改变不会迁移或清理旧文件；标题/作者变化也可能产生新路径。格式或质量变更时可替换本工具已记录的同路径图片，不覆盖未登记的既有文件。空间不足、权限错误、已存在的未登记文件、上游无效图片会显示失败。

### 服务器目录选择

「同步设置 → 选择服务器目录」浏览的是**服务所在机器的目录**，不是 Windows 浏览器所在设备的磁盘。仅允许管理员通过 `JM_DOWNLOAD_ROOTS` 指定的根目录及其子目录，拒绝路径穿越和子目录符号链接。默认根目录为 `DATA_DIR/jm-favorites`；目录在同步时创建。多个根目录用服务器操作系统的路径分隔符分开（Linux/macOS 为 `:`，Windows 为 `;`）。该环境变量变更需要重启。

Docker 中默认 `/app/data/jm-favorites` 已随数据卷持久化。自定义目录需要**同时配置容器内路径和卷挂载**，例如：

```yaml
services:
  comic-api:
    environment:
      - JM_DOWNLOAD_ROOTS=/app/data/jm-favorites:/downloads
    volumes:
      - ./data:/app/data
      - ./comics:/downloads
```

随后在页面选择 `/downloads` 或其子目录。只有设置环境变量而未挂载卷时，文件仍在容器文件系统，不是宿主机目标目录。备份应包含数据库、密钥以及所有自定义下载根目录；站点没有用户认证，不应开放给不可信用户。

协议依据：[JMComic-Crawler-Python v2.7.8 收藏接口与登录实现](https://github.com/hect0x7/JMComic-Crawler-Python/blob/69afe2c38d09960fd43031826af19095f75a42a2/src/jmcomic/jm_client_impl.py#L963-L1019)。`GET /favorite` 使用 `page/folder_id/o`，解析 `list/folder_list/total`，官方页大小为 20；不调用会切换远端收藏状态的 `POST /favorite`。上游接口变化、账号权限或登录失效仍可能导致操作失败。

## 插件扩展

内置插件位于 `src/sources/`；自定义插件放到 `DATA_DIR/plugins/*.py`，重启自动发现。每个模块导出 `create_plugin(store)`，返回 `SourcePlugin` 子类实例，提供唯一 `id`、`name` 与 `capabilities`。插件是**可信的服务器 Python 代码**，没有沙箱；不要安装不可信文件，也不直接兼容 Breeze JavaScript 插件或 Suwayomi APK。

核心接口：

- `search(keyword, page)`：漫画列表，每项含 `id/title/cover/author`。
- `detail(comic_id)`：漫画详情及 `chapters: [{id, name, order}]`。
- `pages(comic_id, chapter_id)`：有序图片 URL 列表。
- `client.request(...)`：图片请求客户端；可选 `transform_image(data, chapter_id, url)` 解码图片。
- 可选 `browse(action, **filters)`、`login(values)`、`logout()`、`account_status()`；通过 `categories/sorts/leaderboard_modes/login_fields` 描述界面。
- 可选 `settings_fields`、`settings()` 描述非敏感设置；`save_settings(values)` 持久化并返回替换用的新插件实例。不要把密码或 Token 放进设置清单，应使用登录凭证接口。

参考 `src/sources/bika.py` 的账号插件和 `src/sources/copy.py` 的游客插件。账号可使用 `store.save_account(plugin.id, data)` 保存，加密由存储层处理。

拷贝适配器参考 [Breeze-plugin-copyComic](https://github.com/deretame/Breeze-plugin-copyComic) 的 API 协议，在 Python 中实现搜索、章节分组分页、图片顺序、推荐与排行榜。`COPY_API_BASE` 可修改 API 地址；上游域名、限流与接口变化可能导致服务不可用。

## API

完整参数见 `/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/sources` | 插件能力与账号状态，不返回凭据 |
| POST / DELETE | `/api/sources/{source}/login` / `/api/sources/{source}/account` | 登录 / 退出 |
| GET | `/api/jm/favorites?page=1&folder_id=0&order=mr` | 禁漫云端收藏与目录，排序支持 mr/mp |
| GET / PUT | `/api/jm/sync/settings` | 同步设置与可用根目录；PUT 保存完整设置 |
| GET | `/api/jm/sync/directories?path=` | 允许范围内的服务器子目录 |
| GET | `/api/jm/sync` | 当前同步、下次执行时间与最近记录（时间为 Unix 秒） |
| POST | `/api/jm/sync/run` | JSON `{}` 同步设置范围，或 `{"album_ids":["123"]}` 指定最多 200 本 |
| POST | `/api/jm/sync/cancel` | 停止本次同步，不关闭定时计划 |
| GET | `/api/search?keyword=&source=&page=&sort=` | 搜索；省略 source 聚合各源；sort 仅对声明 search_sorts 的图源生效 |
| GET | `/api/comic/{source}/{comic_id}` | 详情 |
| GET | `/api/chapter/{source}/{comic_id}/{chapter_id}` | 图片代理列表 |
| GET | `/api/{source}/{action}` | latest / category / leaderboard / random，按能力开放 |
| GET | `/api/library` | 书库 |
| PUT / PATCH / DELETE | `/api/library/{source}/{comic_id}` | 收藏 / 改分类 / 移除 |
| POST | `/api/library/{source}/{comic_id}/refresh` | 检查更新 |
| GET / POST | `/api/downloads` | 列表 / 创建下载 |
| GET / DELETE | `/api/downloads/{task_id}` | 状态 / 删除 |
| POST | `/api/downloads/{task_id}/cancel` 或 `/retry` | 取消 / 重试 |
| GET | `/api/downloads/{task_id}/file` | 下载 PDF |

旧 `/api/bika/login` 保留。旧同步 `/api/download/{source}/{comic_id}/{chapter_id}` 会创建任务并等待，不建议用于长连接；请改用任务 API。下载并发和 PDF 密码由服务统一管理，不再接受调用者覆盖。

## 项目结构

```
main.py                  FastAPI 路由与应用装配
src/config.py            JM / Bika 上游端点与密钥
src/storage.py           SQLite 存储与 AES-GCM 凭据加密
src/sources/             图源插件抽象与内置插件（bika / jm / copy）
src/clients/             上游协议客户端（签名、解密、HTTP 伪装）
src/services/aggregator.py   插件调度、聚合搜索、标题排序
src/services/image_proxy.py  图片代理：SSRF 防护、并发上限、磁盘缓存
src/services/pdf.py          章节图片并发下载与自适应压缩 PDF
src/services/downloads.py    PDF 下载任务队列与生命周期
src/services/jm_sync.py      JM 收藏扫描、定时同步与文件检查点
src/services/local_images.py 服务器目录边界、带宽限速与图片输出
src/services/images.py       JM 图片反切割
src/web/                 原生 JS 单页界面
tests/                   pytest，无需真实账号
scripts/                 Playwright 界面走查与布局断言（开发用）
```

## 测试

```bash
pip install pytest httpx
python -m pytest tests -q
```

测试使用临时数据库和模拟图源，不需要实际账号。`tests.demo_server` 提供离线 WebUI 验证数据，不用于生产部署。

## License

MIT
