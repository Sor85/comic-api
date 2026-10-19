<p align="center">
  <img src="docs/logo.png" alt="Comic 图标" width="128" height="128">
</p>

# Comic API

Python / FastAPI 多图源漫画 WebUI。内置禁漫、哔咔、拷贝漫画、nhentai；前端按插件能力生成浏览入口与登录表单。

## Fork 说明

本项目 fork 自 [lumia1998/comic-api](https://github.com/lumia1998/comic-api)，基于原项目提交 `7410fa6`（2026-09-25）继续开发。原项目已提供多图源聚合搜索、在线阅读、本地书库、PDF 下载及禁漫账号登录；以下为本 fork 新增的功能与修复的问题，详细说明见后文对应章节。

### 新增功能

- **JM 收藏**：浏览云端收藏夹（分页、排序、本页标题／作者筛选、多选），漫画详情中可加入／取消 JM 收藏；收藏状态登记在本地表，按账号隔离。
- **哔咔收藏**：浏览账号全部云端收藏，漫画详情中可加入／取消哔咔收藏，同样使用本地状态表。
- **按下载状态筛选收藏**：JM「全部收藏」与哔咔可按已下载／未下载筛选服务器本地记录的全部收藏，未下载包含本地异常；可通过「更新收藏列表」后台补全记录。JM 自建收藏夹的下载状态筛选仍仅作用于当前页。
- **书库图片同步到本地**：书库漫画（所有图源）按同一目录规则增量下载到服务器目录 `保存目录/图源ID/…`，支持定时同步、按书库分类限定范围、单本／所选同步、限速、图片格式与质量、失败重试、SHA-256 文件检查点；JM、哔咔收藏页的「同步本书」「同步所选」把不在书库中的收藏先加入「JM 收藏」「哔咔收藏」分类再同步。「检查本地漫画」可登记已有目录，避免重复下载；检查与同步结果按漫画保存，书库、收藏卡片和漫画详情显示「已下载」「本地异常」状态，「修复异常漫画」把标题相同、仅「第N話」序号错位的章节目录改名对上，保留有效图片、补下载缺页，损坏或多余图片移入保存目录下的 `.comic-api-repair` 备份目录。下载根目录由 `LIBRARY_DOWNLOAD_ROOTS` 限定；未设置时，在容器中自动列出映射进容器的目录（数据目录除外，第一个作为默认保存位置），再加上 `DATA_DIR/library`。
- **目录迁移**：书库同步窗口中的「迁移目录」选择一个服务器源目录，按漫画 ID 或完整标题识别其中的书库漫画（支持「漫画 ID」「标题 [ID]」「无 ID 标题」「作者 / 标题」「【作者】原标题 / 第N話」等目录结构及无章节子目录的单章漫画），先预览，再把能与章节页码对应且可正常解码的图片按书库当前目录规则移入 `保存目录/图源ID/…` 并登记为本地已下载，缺页由书库同步补齐。可用于更换目录规则后原地重排书库，也可导入旧版收藏同步目录；不覆盖已有文件，单本失败时放回原处，损坏、多余和未识别的文件保留原处。
- **图片同步计划**：支持 5 字段 Cron，按服务器时区执行；「编辑计划」浮窗提供预设与自定义模式，可预览中文说明及后续执行时间，相邻运行至少间隔 5 分钟。
- **云收藏自动同步到书库**：JM 与哔咔收藏可定时增补到「我的书库」的独立分类，不下载图片，默认关闭；入库后由书库图片同步下载图片，「检查本地漫画」不改动书库。
- **搜索增强**：禁漫、哔咔、拷贝三源标签搜索，哔咔分类筛选，作者搜索，以及可选的跨图源搜索结果去重；作者与可搜索标签支持右键／中键在新标签页打开。搜索与发现结果卡片标识已加入书库、已在 JM／哔咔云收藏的状态，合并卡片检查各原始版本。
- **最近搜索恢复页码**：最近搜索保留上次浏览的结果页码与筛选条件，再次点击记录可回到对应页；页码恢复默认开启，可在「设置 → 浏览偏好」中关闭。记录与偏好保存在当前浏览器，不跨设备同步。
- **nhentai 图源**：基于官方 API v2 的搜索、标签搜索、最新、分类、人气榜、随机与阅读；标签搜索内置 JM 主题标签的对应映射，仅收录两边存在且含义对应的项，兼容已登记的繁简名称；可选绑定 API Key 提高限额，PDF 下载使用官方归档接口。
- **章节封面预览**：详情页章节列表可显示章节首图，默认关闭，开关状态保存在当前浏览器。
- **阅读记录存服务器**：观看记录与阅读进度改存服务器 SQLite，多设备共享；支持从浏览器旧数据导入。打开阅读器后首次翻页才开始写入，只打开不翻页不改动「继续阅读」；滚动模式以页码变化为准。
- **图源开关**：每个图源可独立启用或关闭，关闭后退出搜索、发现与收藏入口，并停止相关任务。
- **站点鉴权**：可选的 HTTP Basic 用户名密码鉴权，默认关闭，可在设置中在线修改。
- **统一设置与界面重构**：设置窗口整合站点鉴权与图源管理；收藏、设置、详情界面重构，采用层叠模态窗口和自绘下拉菜单。

### 修复的问题

- 修复 Fake-IP 代理环境下封面加载失败：图片请求固定已校验的公网连接目标，同时保留代理、TLS 与内网防护；Surge 网关、Clash TUN 等透明代理环境无需配置代理变量即可自动识别。
- 修复哔咔旧图片域名返回 301 时图片加载失败：图片下载支持最多 5 次逐跳校验的重定向。
- 完善 JM 本地检查读取漫画详情失败时的提示：显示失败漫画、阶段与原因，并说明已有文件和登记不受影响。
- 修复书库切换时卡片闪烁、JM 收藏封面重复加载：卡片按 ID 复用，CDN 域名轮换不再触发重新渲染，图片代理返回浏览器缓存头。
- 修复再次进入哔咔收藏时重新显示骨架屏：同一页直接复用已有卡片和封面。
- 修复「继续阅读」点击卡片任意位置都直接进入阅读页的问题，现在仅播放按钮直达阅读页；修复手机上阅读页返回按钮常驻高亮。
- 修复搜索后切换到其他页面再返回发现页时，结果页返回按钮退回上一个页面而非首页的问题。
- 修复刷新后搜索结果卡片的「读至」标签偶尔缺失：阅读记录晚于卡片加载完成时，补上对应的已读章节标签。
- 修复刷新搜索页或其他非首页链接时，先闪首页、浅色主题先闪深色及导航选中态跳动的问题。
- 修复超长目录名导致本地漫画误判异常的问题：兼容截短后的漫画／章节目录名，检查与修复可识别保留「第N話」的章节目录；多候选冲突仍报告异常。

## 部署

### 使用预构建镜像（推荐）

镜像由 GitHub Actions 发布到 `ghcr.io/sor85/comic-api`，支持 `linux/amd64`、`linux/arm64`、`linux/arm/v7`。标签：`main` 为最新提交，`sha-<短哈希>` 为指定提交，`x.y.z` 为版本发布。

```yaml
services:
  comic-api:
    image: ghcr.io/sor85/comic-api:main
    container_name: comic-api
    ports:
      - "34587:8699"
    environment:
      - TZ=Asia/Shanghai
      - DATA_DIR=/app/data
      - AUTH_ENABLED=${AUTH_ENABLED:-false}
      - AUTH_USERNAME=${AUTH_USERNAME:-}
      - AUTH_PASSWORD=${AUTH_PASSWORD:-}
      - LIBRARY_DOWNLOAD_ROOTS=${LIBRARY_DOWNLOAD_ROOTS:-/app/data/library}
      - BIKA_ACCOUNT=${BIKA_ACCOUNT:-}
      - BIKA_PASSWORD=${BIKA_PASSWORD:-}
      - COPY_API_BASE=${COPY_API_BASE:-https://api.copy3000.com/api/v3}
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

```bash
docker compose pull
docker compose up -d
```

仓库自带的 `docker-compose.yml` 即上方配置。环境变量说明见 `.env.example`。升级时重复以上两条命令。`./data` 保存数据库、加密密钥与默认同步目录，备份时整个目录一起备份。

### 从源码运行

```bash
cp .env.example .env
docker build -t ghcr.io/sor85/comic-api:main .
docker compose up -d
```

访问 http://localhost:34587 。本地运行使用 `pip install -r requirements.txt && python main.py`，端口 8699。只运行一个服务进程。

图源账号在「设置 → 图源管理 → 登录凭证」绑定，凭据加密保存在 SQLite。站点鉴权默认关闭，可在「设置 → 站点鉴权」开启（HTTP Basic，立即生效）；未开启时不要直接暴露到公网，公网访问需配合 HTTPS。

## 数据与功能

- **混合搜索**：各图源结果统一展示并标注来源，关键词搜索按标题匹配度排序；支持关键词／标签／作者三种搜索方式、哔咔分类筛选与可选的结果去重。点击作者以作者模式搜索 JM、哔咔、nhentai，不再执行普通关键词搜索；JM 使用作者筛选 `main_tag=2`，哔咔使用作者参数 `a`（需要登录），nhentai 使用 `artist:"作者名"`，标签与作者搜索保留各图源原始顺序。作者与可搜索标签可在新标签页打开。搜索与发现结果卡片显示书库、JM／哔咔云收藏状态，以及「读至」已读章节标签；合并卡片按各原始版本判断书库与收藏状态。云收藏标识依据当前账号在服务器本地登记的收藏记录，不额外实时查询上游。
- **最近搜索**：搜索条件与上次浏览的结果页码保存在当前浏览器，默认恢复上次页码；「设置 → 浏览偏好 → 最近搜索恢复页码」可关闭页码恢复。在搜索框重新提交同一条件、关闭恢复开关或旧记录缺少页码时，从第 1 页开始。
- **图片代理**：封面与章节图片经 `/api/image/proxy` 中转并缓存到磁盘，带 SSRF 防护；支持 `HTTP(S)_PROXY`、系统代理与 Fake-IP 透明代理。
- **书库**：收藏、分类、筛选与手动检查章节更新；JM／哔咔云收藏可定时增补到书库。
- **书库本地同步**：书库漫画（所有图源，可按分类限定）按计划增量下载到服务器目录，按 `保存目录/图源ID/…` 存放；下载根目录由 `LIBRARY_DOWNLOAD_ROOTS` 限定。已完整同步且文件未变化的漫画只读取一次详情，新增章节只补齐新增内容；固定格式输出时目标文件已被未登记文件占用会在下载前跳过。
- **下载任务**：章节打包为加密 PDF，支持排队、进度、取消、重试与一键下载全部章节。
- **阅读进度**：保存在服务器，多设备共享；支持续读、滚动／翻页模式。打开阅读器后第一次翻页才写入观看记录，只打开不翻页不改动「继续阅读」。
- **云收藏**：JM、哔咔收藏可浏览、加入／取消收藏，按本地下载状态筛选全部收藏；图片由书库本地同步下载，Docker 中自定义下载目录需同时挂载卷。
- **nhentai**：使用官方 API v2。API Key 在 nhentai「账号设置 → API Keys」生成后填入登录凭证；未绑定时可搜索、浏览、阅读，绑定后限额更高，PDF 下载需要 Key。标签模式下，已登记的 JM 标签会转换为 nhentai 对应标签，例如「女性向」→ `josei`、「全彩」→ `full color`；适用于单源与聚合搜索，不改变其他图源的搜索词。未登记的名称按原文搜索，显式的 `tag:"名称"` 等原生查询不转换；[映射表与范围](docs/nhentai-tags.md)。
- **图源开关**：图源可在「图源管理」独立启用或关闭。

默认数据目录为 `./data`，可用 `DATA_DIR` 修改：

| 路径 | 内容 |
| --- | --- |
| `comic.sqlite` | 站点鉴权、图源凭据密文、书库、下载与阅读记录、收藏状态 |
| `credentials.key` | 凭据加密密钥，必须保密并与数据库一起备份 |
| `downloads/` | 已生成的 PDF |
| `library/` | 默认书库图片同步目录，每个图源一个子目录 |
| `jm-favorites/`、`bika-favorites/` | 旧版收藏同步目录，可用「迁移目录」移入 `library/` |
| `plugins/` | 自定义可信 Python 图源 |

## 插件扩展

内置插件位于 `src/sources/`；自定义插件放到 `DATA_DIR/plugins/*.py`，重启自动发现。每个模块导出 `create_plugin(store)`，返回 `SourcePlugin` 子类实例，提供唯一 `id`、`name` 与 `capabilities`。插件是可信的服务器 Python 代码，没有沙箱。

核心接口：

- `search(keyword, page)`：漫画列表，每项含 `id/title/cover/author`。
- `detail(comic_id)`：漫画详情及 `chapters: [{id, name, order}]`。
- `pages(comic_id, chapter_id)`：有序图片 URL 列表。
- 可选 `search_tag`、`search_author`、`browse`、`login/logout/account_status`、`settings_fields/save_settings`、`download_archive`；声明 `author_search` 能力后参与作者搜索，`search_author(keyword, page, sort)` 沿用 `search_sorts`（未声明排序时可省略 `sort`）。

参考 `src/sources/copy.py`（游客）、`src/sources/bika.py`（账号）和 `src/sources/nhentai.py`（官方 API）。

## API

请求与响应均为 JSON。开启站点鉴权后，除 `GET /health` 外均需 HTTP Basic 认证。服务运行时也可打开 `http://<地址>:<端口>/docs` 查看交互式接口文档（FastAPI 自动生成）。

**图源与设置**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查，公开访问 |
| GET / PUT | `/api/settings/auth` | 站点鉴权；PUT 字段 `enabled/username/password/revision`，密码留空保留原值 |
| GET | `/api/sources` | 图源能力、账号状态与开关 |
| PUT | `/api/sources/{source}/state` | 启用／关闭图源：`{"enabled":true}` |
| POST | `/api/sources/{source}/login` | 绑定账号，字段按图源 `login_fields`（如 nhentai 为 `api_key`） |
| DELETE | `/api/sources/{source}/account` | 解除账号绑定 |
| PUT | `/api/sources/{source}/settings` | 图源设置（如拷贝接口线路） |
| GET | `/api/sources/{source}/search-categories` | 搜索分类目录（哔咔） |

**搜索、浏览与阅读**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/search` | `keyword` 必填；`source` 省略时聚合；`page`、`sort`、`mode=keyword\|tag\|author`、`tag_value`、`category`、`deduplicate` |
| GET | `/api/{source}/{action}` | `action` 为 `latest/category/leaderboard/random`；`page`、`sort`、`mode`（榜单周期）、`name`（分类） |
| GET | `/api/comic/{source}/{comic_id}` | 漫画详情与章节 |
| GET | `/api/chapter/{source}/{comic_id}/{chapter_id}` | 章节图片代理地址列表 |
| GET | `/api/image/proxy` | 图片代理：`source`、`url`、`chapter_id` |

**阅读记录**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/reading` | 全部阅读记录 |
| GET / PUT | `/api/reading/{source}/{comic_id}` | 读取／保存进度；PUT 必填 `chapter_id`、`page`（从 0 开始） |
| DELETE | `/api/reading/history` | 清空「继续阅读」，保留各章进度 |
| POST | `/api/reading/import` | 导入旧版浏览器记录：`comics`、`chapters` |

**书库**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/library` | 书库列表，`category` 筛选（空字符串为未分类） |
| PUT / PATCH / DELETE | `/api/library/{source}/{comic_id}` | 加入／修改分类／移除，字段 `category` |
| POST | `/api/library/{source}/{comic_id}/refresh` | 检查章节更新 |
| GET / PUT | `/api/sources/{source}/library-sync` | 云收藏自动入库（`jm`、`bika`）：`enabled`、`interval_minutes` |
| POST | `/api/sources/{source}/library-sync/run` | 立即执行一次入库 |

**PDF 下载**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/api/downloads` | 任务列表／创建任务：`source`、`comic_id`、`chapter_id`、`title`、`chapter` |
| GET / DELETE | `/api/downloads/{task_id}` | 任务状态／删除任务及文件 |
| POST | `/api/downloads/{task_id}/cancel` | 取消 |
| POST | `/api/downloads/{task_id}/retry` | 重试 |
| GET | `/api/downloads/{task_id}/file` | 取回 PDF |
| GET | `/api/download/{source}/{comic_id}/{chapter_id}` | 旧接口：创建任务并等待完成，不建议使用 |

**云收藏（JM／哔咔）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/jm/favorites` | JM 收藏：`page`、`folder_id`（0 为全部）、`order=mr\|mp` |
| GET | `/api/bika/favorites` | 哔咔收藏：`page`、`sort=dd\|da\|ld\|vd` |
| GET / POST / DELETE | `/api/{jm\|bika}/favorites/{comic_id}` | 本地登记的收藏状态／加入云收藏／取消云收藏 |

| GET | `/api/{jm\|bika}/favorite-ids?ids=a,b` | 批量查询当前账号在本地登记的已收藏 ID，不请求上游；单次最多 200 本，返回 `{"ids":[...]}`，供结果卡片显示云收藏标识 |
| GET | `/api/{jm\|bika}/favorites-catalog` | 本地记录的全部收藏及其书库本地状态：`local=downloaded\|missing`（未下载含异常）、`page`；返回筛选结果、各状态总数、列表是否完整与更新进度 |
| POST | `/api/{jm\|bika}/favorites-catalog/refresh` | 后台读取全部收藏分页并替换本地记录：`sort`（JM `mr\|mp`，哔咔 `dd\|da\|ld\|vd`） |

**书库图片同步**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / PUT | `/api/library/sync/settings` | 同步设置：`enabled`、`cron`（5 字段 Cron 表达式，服务器时区，相邻运行至少间隔 5 分钟）、`download_dir`、`speed_limit_kbps`、`image_format`、`quality`、`directory_rule`（`id`／`title`／`title_plain`／`author`）、`retries`、`categories`（书库分类，空为整个书库，`""` 为未分类）；返回可选分类及书本数 |
| GET | `/api/cron/preview?expression=` | 校验 Cron 表达式，返回规范化表达式、中文说明、服务器时区和接下来 5 次运行时间 |
| GET | `/api/library/sync/directories` | 浏览允许范围内的服务器目录：`path` |
| GET | `/api/library/sync` | 当前任务、各本地状态总数、下次执行时间与最近记录 |
| POST | `/api/library/sync/run` | 立即同步设置范围内的书库；`{"items":[{"source":"jm","id":"123"}]}` 只同步指定漫画（最多 200 本），不在书库中的先加入书库 |
| POST | `/api/library/sync/check-local` | 检查并登记本地已有漫画，不下载图片 |
| POST | `/api/library/sync/repair` | 修复本地检查标记为异常的漫画：序号错位的章节目录改名对上，保留有效图片、补下载缺页，损坏或多余图片移入 `.comic-api-repair` 备份目录；`items` 限定范围 |
| GET | `/api/library/sync/local-status?source=jm&ids=a,b` | 指定图源各漫画的本地状态（已下载 / 异常 / 未下载），单次最多 200 本 |
| POST | `/api/library/sync/cancel` | 停止当前任务 |
| GET | `/api/library/migration` | 目录迁移状态：源目录、目标目录与规则、预览结果与未识别内容 |
| GET | `/api/library/migration/directories` | 浏览可作为迁移源的服务器目录（书库下载根目录及旧版收藏同步目录）：`path` |
| POST | `/api/library/migration/preview` | `{"source_dir":"…"}` 扫描源目录生成迁移预览，不移动文件 |
| POST | `/api/library/migration/run` | 按最近一次预览执行迁移；同步设置变化后需重新预览 |

## 项目结构

```
main.py                  FastAPI 路由与应用装配
src/auth.py              站点 HTTP Basic 鉴权
src/storage.py           SQLite 存储与 AES-GCM 凭据加密
src/sources/             图源插件（bika / jm / copy / nhentai）
src/clients/             上游协议客户端
src/services/            聚合搜索、图片代理、PDF 下载、书库图片同步
src/web/                 原生 JS 单页界面
tests/                   pytest 与前端单元测试，无需真实账号
```

## 测试

```bash
pip install pytest httpx
python -m pytest tests -q
node --test tests/*-ui.test.cjs
```

## License

MIT。
