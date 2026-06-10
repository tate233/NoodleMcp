# Catch Knowledge

Catch Knowledge 是一个个人面经知识库系统。它负责从小红书、手动上传、QQ 私聊等入口收集面经材料，经过 OCR 和 LLM 结构化分析后写入数据库，并导出成 Obsidian 可直接打开的 Markdown 知识库。

项目定位不是一个完整社交产品，而是一个长期自动运行的“面经采集与整理 Agent”：每天抓取、手动补充、自动清洗、自动归类、自动沉淀到知识库。

## 当前链路

主链路如下：

1. 内容入口接收原始材料。
2. 原始帖子先写入 `raw_posts`。
3. 如果有图片，保存图片链接或本地上传图片。
4. OCR 识别图片文字，写入 `raw_image_text`。
5. 合并正文和 OCR 文本，生成 `raw_text`。
6. LLM 分析 `raw_text`，提取公司、岗位、题目、知识点、算法题、摘要等结构化字段。
7. 分析结果写入 `post_analysis`。
8. 构建或增量更新 `canonical_questions`，用于题目归并、知识点聚合和频次统计。
9. 导出到 `knowledge_base/`，用 Obsidian 打开阅读。

当前支持三种入口：

- 小红书自动抓取：`xiaohongshu-mcp` + `run-once` 或 `schedule`
- 手动上传：Web 控制台或 `manual-import`
- QQ 私聊上传：NapCat + `qq-adapter`

## 核心目录

```text
catch_knowledge/
  src/catch_knowledge/
    adapters/          QQ/NapCat 等外部入口适配
    exporters/         Markdown/Obsidian 导出
    indexing/          题目归并与知识点索引
    llm/               LLM 分析
    ocr/               OCR 识别
    pipeline/          主处理流程
    sources/           小红书、牛客等内容源
    web/               Web 控制台
  data/                本地数据、上传文件、缓存
  knowledge_base/      Obsidian Markdown 知识库
  xiaohongshu-mcp/     小红书 MCP 子模块
  docker-compose.yml   PostgreSQL 容器
```

## 主要数据表

### `raw_posts`

保存原始材料：

- `raw_source_text`：原始正文
- `raw_image_text`：OCR 识别出的图片文字
- `raw_text`：正文和 OCR 文本合并后的 LLM 输入
- `image_urls`：图片链接
- `status`：处理状态

### `post_analysis`

保存 LLM 分析结果：

- `content_type`：内容类型，例如 `interview_note`、`knowledge_snippet`、`algorithm_snippet`
- `company`：公司
- `job_role`：岗位
- `job_direction`：方向
- `interview_rounds`：轮次
- `interview_questions`：面试题
- `question_points`：知识点
- `summary`：摘要
- `normalized_json`：完整结构化结果、fallback 信息、LLM 错误信息

### `canonical_questions`

保存归并后的题目索引：

- `kind`：`interview` 或 `algorithm`
- `knowledge_point`：所属知识点
- `canonical_text`：归并后的标准题目
- `frequency`：出现频次
- `source_raw_post_ids`：来源记录 ID
- `variants`：原始题目变体和细分考点

### `kb_documents`

保存已经导出的 Obsidian 单篇面经路径。

## 环境准备

推荐 Python 3.9 或更高版本。当前项目本机测试主要使用 Python 3.9。

安装基础依赖：

```powershell
pip install -e .
```

如果使用 Web 控制台、QQ 适配器、PostgreSQL：

```powershell
pip install -e .[web,postgres]
```

如果需要开发测试：

```powershell
pip install -e .[dev,web,postgres]
```

## 配置 `.env`

先复制示例配置：

```powershell
Copy-Item .env.example .env
```

部署推荐使用 PostgreSQL：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/catch_knowledge
```

LLM 配置：

```env
OPENAI_API_KEY=你的主模型 API Key
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_MODEL=deepseek-ai/DeepSeek-V3

OPENAI_BACKUP_API_KEY=你的备用模型 API Key
OPENAI_BACKUP_BASE_URL=https://api.moonshot.cn/v1
OPENAI_BACKUP_MODEL=kimi-k2-0711-preview

LLM_RETRY_COUNT=2
LLM_RETRY_BACKOFF_SECONDS=3
LLM_RETRY_BACKOFF_MULTIPLIER=2
LLM_REQUEST_TIMEOUT_SECONDS=180
LLM_QUEUE_RETRY_DELAY_SECONDS=300
LLM_QUEUE_MAX_ATTEMPTS=6
```

火山 OCR 配置：

```env
OCR_ENABLED=true
OCR_PROVIDER=volcengine
OCR_DOWNLOAD_TIMEOUT_SECONDS=30
OCR_MAX_IMAGES_PER_POST=9

VOLCENGINE_OCR_AK=你的 AK
VOLCENGINE_OCR_SK=你的 SK
VOLCENGINE_OCR_ENDPOINT=https://visual.volcengineapi.com
VOLCENGINE_OCR_REGION=cn-north-1
VOLCENGINE_OCR_SERVICE=cv
VOLCENGINE_OCR_SCENE=general
VOLCENGINE_OCR_MODE=default
VOLCENGINE_OCR_FILTER_THRESH=80
VOLCENGINE_OCR_HALF_TO_FULL=false
```

小红书 MCP 配置：

```env
SOURCE_PLATFORM=xiaohongshu_mcp
XHS_MCP_BASE_URL=http://127.0.0.1:18060
XHS_KEYWORDS=后端 面经
XHS_SEARCH_SORT_BY=最新
XHS_SEARCH_PUBLISH_TIME=一天内
XHS_MAX_RESULTS_PER_KEYWORD=10
XHS_MIN_DELAY_SECONDS=10
XHS_MAX_DELAY_SECONDS=15
```

定时任务配置：

```env
SCHEDULE_CRON=0 0 * * *
TIMEZONE=Asia/Shanghai
```

## 本地启动 PostgreSQL

项目已经提供 `docker-compose.yml`。本机或服务器安装 Docker 后执行：

```powershell
docker compose up -d
```

检查容器：

```powershell
docker compose ps
```

初始化数据库表：

```powershell
python -m catch_knowledge.cli init-db
```

如果之前有 SQLite 数据，可以迁移到当前 `DATABASE_URL`：

```powershell
python -m catch_knowledge.cli migrate-sqlite-to-db --sqlite-path ./data/catch_knowledge.db
```

## 小红书 MCP 启动

进入子模块目录：

```powershell
cd .\xiaohongshu-mcp
go run . -headless=false
```

回到项目根目录检查登录状态：

```powershell
python -m catch_knowledge.cli xhs-mcp-status
```

如果未登录，可以获取二维码：

```powershell
python -m catch_knowledge.cli xhs-mcp-qrcode
```

登录成功后可以预览搜索结果：

```powershell
python -m catch_knowledge.cli xhs-search
```

执行一次完整抓取：

```powershell
python -m catch_knowledge.cli run-once
```

`run-once` 会执行：

1. 从小红书 MCP 搜索候选帖子。
2. 拉取帖子详情。
3. 原始数据入库。
4. OCR 识别图片。
5. LLM 结构化分析。
6. 增量更新题目索引。
7. 增量导出 Obsidian。

## Web 控制台

启动 Web 控制台：

```powershell
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

Web 控制台主要用于：

- 手动上传面经文本
- 手动上传图片并走 OCR
- 查看最近记录
- 查看 LLM 错误和 fallback 原因
- 单条重新分析
- 编辑记录
- 删除记录
- 手动触发全量索引和全量导出

常规上传、编辑、删除、重新分析都会走增量同步。只有控制台里的全量按钮才会重新构建全量知识库。

## 手动导入

直接传文字：

```powershell
python -m catch_knowledge.cli manual-import --title "字节后端二面" --text "这里粘贴面经正文"
```

传文本文件：

```powershell
python -m catch_knowledge.cli manual-import --title "美团一面" --text-file .\example.md
```

传图片：

```powershell
python -m catch_knowledge.cli manual-import --title "截图面经" --image .\1.png --image .\2.png
```

图文混合：

```powershell
python -m catch_knowledge.cli manual-import --title "京东一面" --text "补充说明" --image .\1.png
```

## QQ 私聊上传

QQ 接入使用 NapCat。推荐在服务器上使用 HTTP Client 上报到 `qq-adapter`，并配置 token。

启动主 Web 服务：

```powershell
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
```

启动 QQ adapter：

```powershell
python -m catch_knowledge.cli qq-adapter `
  --host 127.0.0.1 `
  --port 8090 `
  --ingest-base-url http://127.0.0.1:8000 `
  --napcat-api-base-url http://127.0.0.1:3000 `
  --napcat-access-token 你的_NapCat_HTTP_Server_Token `
  --webhook-secret 你的_Webhook_Token
```

NapCat 里配置 HTTP Client：

```text
URL: http://127.0.0.1:8090/qq/webhook
Authorization: Bearer 你的_Webhook_Token
消息格式: Array
```

NapCat HTTP Server 如果设置了 token，`qq-adapter` 调用 NapCat 发回复和取图片时需要传：

```text
--napcat-access-token 你的_NapCat_HTTP_Server_Token
```

之后给这个 QQ 号发私聊即可：

- 纯文字面经
- 图片截图
- 图文混合内容

QQ adapter 会把内容转发到 Web API，然后自动走 OCR、LLM、入库和 Obsidian 增量同步。

## Web API

健康检查：

```text
GET /api/health
```

纯文本上传：

```text
POST /api/ingest/text
Content-Type: application/json
```

示例：

```json
{
  "title": "问到 HTTPS 证书伪造怎么办",
  "text": "问到 HTTPS，问了证书伪造怎么办",
  "source": "qq",
  "sender": "tate"
}
```

图文混合上传：

```text
POST /api/ingest/message
Content-Type: multipart/form-data
```

可传字段：

- `title`
- `text`
- `source_url`
- `author`
- `source`
- `sender`
- `files`

## Obsidian 知识库

导出目录：

```text
knowledge_base/
```

用 Obsidian 打开 `knowledge_base/` 作为 Vault。

当前导出结构：

```text
knowledge_base/
  面经/
    公司名/
      单篇面经.md
  公司/
    公司名.md
  面试题/
    知识点.md
  算法题/
    算法题.md
  面经知识库.md
```

日常使用时，上传和自动抓取会增量更新知识库。只有需要彻底重算时才手动执行：

```powershell
python -m catch_knowledge.cli build-question-index
python -m catch_knowledge.cli export-obsidian
```

如果在 Obsidian 里改了单篇面经，可以同步回数据库：

```powershell
python -m catch_knowledge.cli sync-obsidian
```

注意：`sync-obsidian` 只同步单篇面经，不同步公司页、面试题页、算法题页这些自动生成的聚合页。

## LLM 与 OCR 补跑

检查 LLM 连通性：

```powershell
python -m catch_knowledge.cli llm-check
```

补跑 LLM fallback 记录：

```powershell
python -m catch_knowledge.cli reanalyze-fallback
```

处理 LLM 后台重试队列：

```powershell
python -m catch_knowledge.cli process-llm-retry-queue --limit 20
```

补跑题目为空的记录：

```powershell
python -m catch_knowledge.cli reanalyze-missing-questions
```

补跑 OCR：

```powershell
python -m catch_knowledge.cli rerun-ocr
```

## 定时任务

使用内置 scheduler：

```powershell
python -m catch_knowledge.cli schedule
```

它会根据 `.env` 里的 `SCHEDULE_CRON` 执行 `run-once`。

也可以用系统级定时任务，例如 Linux crontab：

```cron
0 0 * * * cd /opt/catch_knowledge && /opt/catch_knowledge/.venv/bin/python -m catch_knowledge.cli run-once >> /opt/catch_knowledge/logs/run-once.log 2>&1
*/10 * * * * cd /opt/catch_knowledge && /opt/catch_knowledge/.venv/bin/python -m catch_knowledge.cli process-llm-retry-queue --limit 20 >> /opt/catch_knowledge/logs/llm-retry.log 2>&1
```

## 服务器部署建议

推荐服务器上长期运行这些组件：

- PostgreSQL：Docker Compose
- Web 控制台：`python -m catch_knowledge.cli web`
- QQ adapter：`python -m catch_knowledge.cli qq-adapter`
- NapCat：登录 QQ 并上报私聊消息
- xiaohongshu-mcp：保持登录态，用于每日抓取
- 定时任务：`schedule` 或系统 crontab

推荐端口：

```text
PostgreSQL:       127.0.0.1:5432
Web 控制台:       127.0.0.1:8000
QQ adapter:       127.0.0.1:8090
NapCat HTTP API:  127.0.0.1:3000
xiaohongshu-mcp:  127.0.0.1:18060
```

如果只自己使用，最稳妥的方式是：

1. 所有服务只监听 `127.0.0.1`。
2. 通过 SSH 隧道访问 Web 控制台。
3. 不把 NapCat、PostgreSQL、xhs-mcp 直接暴露到公网。

SSH 隧道示例：

```powershell
ssh -L 8000:127.0.0.1:8000 user@your-server
```

然后本机打开：

```text
http://127.0.0.1:8000
```

如果必须公网访问 Web 控制台，建议使用 Nginx 反代并加 HTTPS、Basic Auth 或其他鉴权。不要把 NapCat HTTP API、PostgreSQL、xiaohongshu-mcp 直接暴露到公网。

## NapCat 公网安全

如果 NapCat 和 Catch Knowledge 都部署在同一台服务器，推荐全部使用本机地址：

```text
NapCat -> QQ adapter: http://127.0.0.1:8090/qq/webhook
QQ adapter -> Web:   http://127.0.0.1:8000
QQ adapter -> NapCat:http://127.0.0.1:3000
```

需要配置两类 token：

1. NapCat HTTP Server token

用于保护 NapCat API，例如发消息、取图片。

`qq-adapter` 启动时传：

```powershell
--napcat-access-token 你的_NapCat_HTTP_Server_Token
```

2. QQ adapter webhook token

用于保护 `POST /qq/webhook`，防止别人伪造请求往你的系统塞数据。

`qq-adapter` 启动时传：

```powershell
--webhook-secret 你的_Webhook_Token
```

NapCat HTTP Client 请求头设置：

```text
Authorization: Bearer 你的_Webhook_Token
```

如果不走公网，并且所有服务都绑定 `127.0.0.1`，安全风险会小很多。但部署到服务器后，仍然建议配置 token。

## 推荐部署步骤

以下以 Linux 服务器为例。

1. 拉取代码

```bash
git clone https://github.com/tate233/NoodleMcp.git catch_knowledge
cd catch_knowledge
```

2. 准备 Python 虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[web,postgres]"
```

3. 准备 `.env`

```bash
cp .env.example .env
```

然后填写：

- `DATABASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`
- `OPENAI_BACKUP_API_KEY`
- `OPENAI_BACKUP_BASE_URL`
- `OPENAI_BACKUP_MODEL`
- `VOLCENGINE_OCR_AK`
- `VOLCENGINE_OCR_SK`
- `XHS_MCP_BASE_URL`
- `SCHEDULE_CRON`

4. 启动 PostgreSQL

```bash
docker compose up -d
python -m catch_knowledge.cli init-db
```

5. 启动 Web 控制台

```bash
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
```

6. 启动 QQ adapter

```bash
python -m catch_knowledge.cli qq-adapter \
  --host 127.0.0.1 \
  --port 8090 \
  --ingest-base-url http://127.0.0.1:8000 \
  --napcat-api-base-url http://127.0.0.1:3000 \
  --napcat-access-token 你的_NapCat_HTTP_Server_Token \
  --webhook-secret 你的_Webhook_Token
```

7. 启动小红书 MCP

```bash
cd xiaohongshu-mcp
go run . -headless=true
```

8. 配置定时任务

可以使用内置 scheduler：

```bash
python -m catch_knowledge.cli schedule
```

也可以使用 crontab。生产环境更推荐 crontab 或 systemd timer，因为进程退出后更容易发现和恢复。

## systemd 示例

Web 服务示例：

```ini
[Unit]
Description=Catch Knowledge Web
After=network.target docker.service

[Service]
WorkingDirectory=/opt/catch_knowledge
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/catch_knowledge/.venv/bin/python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

QQ adapter 示例：

```ini
[Unit]
Description=Catch Knowledge QQ Adapter
After=network.target

[Service]
WorkingDirectory=/opt/catch_knowledge
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/catch_knowledge/.venv/bin/python -m catch_knowledge.cli qq-adapter --host 127.0.0.1 --port 8090 --ingest-base-url http://127.0.0.1:8000 --napcat-api-base-url http://127.0.0.1:3000 --napcat-access-token 你的_NapCat_HTTP_Server_Token --webhook-secret 你的_Webhook_Token
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

定时抓取可以先用 crontab，不一定一开始就写 systemd timer。

## 常用命令速查

```powershell
python -m catch_knowledge.cli init-db
python -m catch_knowledge.cli llm-check
python -m catch_knowledge.cli xhs-mcp-status
python -m catch_knowledge.cli xhs-search
python -m catch_knowledge.cli run-once
python -m catch_knowledge.cli schedule
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
python -m catch_knowledge.cli qq-adapter --host 127.0.0.1 --port 8090 --ingest-base-url http://127.0.0.1:8000
python -m catch_knowledge.cli manual-import --title "手动面经" --text "这里粘贴正文"
python -m catch_knowledge.cli rerun-ocr
python -m catch_knowledge.cli reanalyze-fallback
python -m catch_knowledge.cli process-llm-retry-queue --limit 20
python -m catch_knowledge.cli build-question-index
python -m catch_knowledge.cli export-obsidian
python -m catch_knowledge.cli sync-obsidian
```

## 日常使用建议

日常最常用的路径是：

1. 服务器定时跑 `run-once` 抓小红书面经。
2. 平时看到零散面经，直接发给 QQ 号。
3. 图片截图通过 QQ 上传，系统自动 OCR。
4. Web 控制台只用来检查失败记录、手动重试、编辑或删除。
5. Obsidian 只负责阅读、复习和少量人工修正。
6. LLM fallback 由 `process-llm-retry-queue` 定时补跑。

如果 LLM 出现大量 `Connection error`，优先检查代理、网络和模型网关。这个项目的 LLM 客户端已经有重试、备用模型和后台补跑，但稳定网络仍然很重要。
