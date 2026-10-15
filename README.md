<p align="center">
  <img src="docs/logo.png" alt="Comic 图标" width="128" height="128">
</p>

# Comic API

Python / FastAPI 多图源漫画 WebUI。内置禁漫、哔咔、拷贝漫画、nhentai；前端按插件能力生成浏览入口与登录表单。

## Fork 说明

本项目 fork 自 [lumia1998/comic-api](https://github.com/lumia1998/comic-api)，基于原项目提交 `7410fa6`（2026-09-25）继续开发。原项目已提供多图源聚合搜索、在线阅读、本地书库、PDF 下载及禁漫账号登录；以下为本 fork 新增的功能与修复的问题，详细说明见后文对应章节。

### 新增功能

- **JM 收藏**：浏览云端收藏夹（分页、排序、本页筛选、多选），漫画详情中可加入 JM 收藏；收藏状态登记在本地表，按账号隔离。
- **哔咔收藏**：浏览账号全部云端收藏，漫画详情中可加入哔咔收藏，同样使用本地状态表。
- **收藏图片同步到本地**：JM 与哔咔收藏可按目录规则增量下载到服务器目录，支持定时同步、单本／所选同步、限速、图片格式与质量、失败重试、SHA-256 文件检查点；「检查本地漫画」可登记已有目录，避免重复下载。JM 额外支持按收藏夹限定范围。下载根目录分别由 `JM_DOWNLOAD_ROOTS`、`BIKA_DOWNLOAD_ROOTS` 限定。
- **云收藏自动同步到书库**：JM 与哔咔收藏可定时增补到「我的书库」的独立分类，不下载图片，默认关闭。
- **搜索增强**：禁漫、哔咔、拷贝三源标签搜索，哔咔分类筛选，作者搜索，以及可选的跨图源搜索结果去重。
- **nhentai 图源**：基于官方 API v2 的搜索、标签搜索、最新、分类、人气榜、随机与阅读；可选绑定 API Key 提高限额，PDF 下载使用官方归档接口。
- **章节封面预览**：详情页章节列表可显示章节首图，默认关闭，开关状态保存在当前浏览器。
- **阅读记录存服务器**：观看记录与阅读进度改存服务器 SQLite，多设备共享；支持从浏览器旧数据导入。
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
      - JM_DOWNLOAD_ROOTS=${JM_DOWNLOAD_ROOTS:-/app/data/jm-favorites}
      - BIKA_DOWNLOAD_ROOTS=${BIKA_DOWNLOAD_ROOTS:-/app/data/bika-favorites}
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

环境变量说明见 `.env.example`。升级时重复以上两条命令。`./data` 保存数据库、加密密钥与默认同步目录，备份时整个目录一起备份。

### 从源码运行

```bash
cp .env.example .env
docker compose up -d --build
```

访问 http://localhost:34587 。本地运行使用 `pip install -r requirements.txt && python main.py`，端口 8699。只运行一个服务进程。

图源账号在「设置 → 图源管理 → 登录凭证」绑定，凭据加密保存在 SQLite。站点鉴权默认关闭，可在「设置 → 站点鉴权」开启（HTTP Basic，立即生效）；未开启时不要直接暴露到公网，公网访问需配合 HTTPS。

## 数据与功能

- **混合搜索**：各图源结果统一展示并标注来源，按标题匹配度排序；支持关键词／标签搜索、作者搜索、哔咔分类筛选与可选的结果去重。
- **图片代理**：封面与章节图片经 `/api/image/proxy` 中转并缓存到磁盘，带 SSRF 防护；支持 `HTTP(S)_PROXY`、系统代理与 Fake-IP 透明代理。
- **书库**：收藏、分类、筛选与手动检查章节更新；JM／哔咔云收藏可定时增补到书库。
- **下载任务**：章节打包为加密 PDF，支持排队、进度、取消、重试与一键下载全部章节。
- **阅读进度**：保存在服务器，多设备共享；支持续读、滚动／翻页模式。
- **云收藏与图片同步**：JM、哔咔收藏可浏览、加入收藏，并按目录规则增量同步图片到服务器目录。下载根目录由 `JM_DOWNLOAD_ROOTS`、`BIKA_DOWNLOAD_ROOTS` 限定，Docker 中自定义目录需同时挂载卷。
- **nhentai**：使用官方 API v2。API Key 在 nhentai「账号设置 → API Keys」生成后填入登录凭证；未绑定时可搜索、浏览、阅读，绑定后限额更高，PDF 下载需要 Key。
- **图源开关**：图源可在「图源管理」独立启用或关闭。

默认数据目录为 `./data`，可用 `DATA_DIR` 修改：

| 路径 | 内容 |
| --- | --- |
| `comic.sqlite` | 站点鉴权、图源凭据密文、书库、下载与阅读记录、收藏状态 |
| `credentials.key` | 凭据加密密钥，必须保密并与数据库一起备份 |
| `downloads/` | 已生成的 PDF |
| `jm-favorites/`、`bika-favorites/` | 默认收藏图片同步目录 |
| `plugins/` | 自定义可信 Python 图源 |

## 插件扩展

内置插件位于 `src/sources/`；自定义插件放到 `DATA_DIR/plugins/*.py`，重启自动发现。每个模块导出 `create_plugin(store)`，返回 `SourcePlugin` 子类实例，提供唯一 `id`、`name` 与 `capabilities`。插件是可信的服务器 Python 代码，没有沙箱。

核心接口：

- `search(keyword, page)`：漫画列表，每项含 `id/title/cover/author`。
- `detail(comic_id)`：漫画详情及 `chapters: [{id, name, order}]`。
- `pages(comic_id, chapter_id)`：有序图片 URL 列表。
- 可选 `search_tag`、`browse`、`login/logout/account_status`、`settings_fields/save_settings`、`download_archive`。

参考 `src/sources/copy.py`（游客）、`src/sources/bika.py`（账号）和 `src/sources/nhentai.py`（官方 API）。

## API

完整参数见 `/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/sources` | 插件能力、账号状态与开关 |
| GET | `/api/search?keyword=&source=&page=` | 搜索；省略 source 聚合各源 |
| GET | `/api/comic/{source}/{comic_id}` | 详情 |
| GET | `/api/chapter/{source}/{comic_id}/{chapter_id}` | 图片代理列表 |
| GET | `/api/{source}/{action}` | latest / category / leaderboard / random |
| GET | `/api/library` | 书库 |
| GET / POST | `/api/downloads` | 列表 / 创建下载 |
| GET | `/api/downloads/{task_id}/file` | 下载 PDF |

## 项目结构

```
main.py                  FastAPI 路由与应用装配
src/auth.py              站点 HTTP Basic 鉴权
src/storage.py           SQLite 存储与 AES-GCM 凭据加密
src/sources/             图源插件（bika / jm / copy / nhentai）
src/clients/             上游协议客户端
src/services/            聚合搜索、图片代理、PDF 下载、收藏同步
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

MIT。第三方代码许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
