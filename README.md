# Catch Knowledge

一个面向个人使用的面经知识库工具：从内容源采集帖子，保存原始数据，调用 OCR 和大模型做结构化分析，并导出成可检索的知识库内容。

## 当前状态

当前基础链路已经打通：

- 使用 `xiaohongshu-mcp` 抓取帖子
- 原始帖子先入库
- 保存图片链接
- 调用火山 OCR 提取图片文字
- 将“正文 + OCR 文本”一起交给 LLM
- 提取面试题、考点、公司、岗位、轮次等结构化字段
- 导出 Markdown 知识库

当前开发期默认使用 `SQLite`，正式部署目标是切换到 `PostgreSQL`。

## 当前能力

- 支持 `xiaohongshu-mcp` 作为内容源
- 支持原始帖子、OCR 文本、结构化分析结果入库
- 支持 `rerun-ocr` 补跑图片 OCR
- 支持 `reanalyze-fallback` 补跑 LLM fallback 记录
- 支持 `reanalyze-missing-questions` 补跑题目为空的记录
- 支持构建 `canonical_questions` 题目索引，用于按知识点/算法题统计频次和来源
- 支持导出 Markdown 知识库
- 支持切换数据库到 PostgreSQL

## 当前数据流

1. 启动 `xiaohongshu-mcp`
2. 用关键词搜索候选帖子
3. 拉取帖子详情并写入 `raw_posts`
4. 保存图片链接到数据库
5. 下载图片并调用火山 OCR
6. 将 OCR 文本并入 `raw_text`
7. 调用 LLM 提取结构化信息，写入 `post_analysis`
8. 构建题目索引 `canonical_questions`
9. 导出到 `knowledge_base/`

## 主要数据表

### `raw_posts`

- 原始帖子数据
- 原帖正文 `raw_source_text`
- OCR 文本 `raw_image_text`
- 合并后的分析输入 `raw_text`
- 图片链接 `image_urls`

### `post_analysis`

- `company`
- `job_role`
- `job_direction`
- `interview_rounds`
- `tags`
- `interview_questions`
- `question_points`
- `summary`
- `normalized_json`

### `kb_documents`

- 导出的 Markdown 文档路径

### `canonical_questions`

- 归并后的题目文本 `canonical_text`
- 题目类型 `kind`，当前包含 `interview` 和 `algorithm`
- 所属知识点 `knowledge_point`
- 出现频次 `frequency`
- 来源面经 ID `source_raw_post_ids`
- 原始题目变体 `variants`

## 常用命令

### 抓取与分析

```powershell
python -m catch_knowledge.cli run-once
```

执行一次完整流程：抓取、OCR、入库、LLM 分析、导出。

### 补跑 OCR

```powershell
python -m catch_knowledge.cli rerun-ocr
```

只处理“有图片链接但 `raw_image_text` 为空”的记录。

### 手动导入面经

支持本地文本、Markdown、图片混合导入，导入后会自动走 OCR、LLM 分析、题目索引重建和 Obsidian 导出：

```powershell
python -m catch_knowledge.cli manual-import --text-file .\example.md --image .\1.png --image .\2.png --title "字节后端二面"
```

也支持直接传纯文本：

```powershell
python -m catch_knowledge.cli manual-import --text "这里直接粘贴面经正文" --title "手动上传面经"
```

### Web 操作台

如果你不想每次都走 CLI，可以启动一个轻量 Web 操作台，用来上传材料、查看最近记录，并一键触发索引重建和 Obsidian 导出：

```powershell
pip install -e .[web]
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
```

默认打开：

```text
http://127.0.0.1:8000
```

这个 Web 入口负责“上传和触发处理”，知识库阅读和精修仍然建议放在 Obsidian 里完成。

### QQ 接入（NapCat）

如果你想直接通过 QQ 私聊把文字或截图投喂进系统，推荐用 `NapCatQQ + 本项目自带 qq-adapter`：

1. 先启动主 Web 服务

```powershell
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
```

2. 再启动 QQ adapter

```powershell
python -m catch_knowledge.cli qq-adapter --host 127.0.0.1 --port 8090 --ingest-base-url http://127.0.0.1:8000 --napcat-api-base-url http://127.0.0.1:3000
```

3. 在 NapCat 里把私聊消息事件上报到：

```text
http://127.0.0.1:8090/qq/webhook
```

4. 之后你给这个 QQ 号发：
- 纯文字
- 图片
- 图文混合

adapter 会自动转发给：
- `POST /api/ingest/text`
- 或 `POST /api/ingest/message`

然后由主系统完成：
- OCR
- LLM 分析
- 入库
- 题目索引重建
- Obsidian 导出

如果配置了 NapCat API 地址，adapter 还会自动给你回一条简短结果，比如：

```text
已收录
类型：knowledge_snippet
状态：processed
题目：问到https，问了证书伪造怎么办
记录ID：22
```

### 对话入口用的最小上传 API

为了后续接微信 / QQ，这个 Web 服务现在也提供了最小上传 API。聊天侧只需要把文字和图片转发到这里，不需要直接调用 CLI。

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
  "title": "问到https，问了证书伪造怎么办",
  "text": "问到https，问了证书伪造怎么办",
  "source": "wechat",
  "sender": "tate"
}
```

文本 + 图片混合上传：

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
- `files`（可多文件，支持 txt/md/图片）

返回结果会带：

- `raw_post_id`
- `status`
- `content_type`
- `interview_questions`
- `question_points`
- `summary`

这样微信 / QQ adapter 只要做一件事：

1. 收消息
2. 把文字和附件转发到 `/api/ingest/message`
3. 把返回结果回显给你

### 补跑 LLM fallback

```powershell
python -m catch_knowledge.cli reanalyze-fallback
```

只处理之前因为 LLM 网络或模型问题而走 fallback 的记录。

### 补跑题目为空的记录

```powershell
python -m catch_knowledge.cli reanalyze-missing-questions
```

### 检查模型连通性

```powershell
python -m catch_knowledge.cli llm-check
```

### 初始化当前数据库

```powershell
python -m catch_knowledge.cli init-db
```

### 把 SQLite 数据迁移到当前数据库

```powershell
python -m catch_knowledge.cli migrate-sqlite-to-db --sqlite-path ./data/catch_knowledge.db
```

这个命令适合你后面切到 PostgreSQL 时使用。

### 导出 Obsidian 知识库

建议先构建题目索引，再导出 Obsidian：

```powershell
python -m catch_knowledge.cli build-question-index
```

这个命令会按知识点局部归并题目，并记录频次和来源。目前默认使用快速规则归并，避免每次把全量题目塞给 LLM。

当题目落入 `未分类` 时，系统还会额外记录一个受控扩展建议，不会直接修改主 taxonomy。你可以用下面的命令查看候选目录：

```powershell
python -m catch_knowledge.cli list-taxonomy-suggestions
```

题目索引采用固定一级 taxonomy 作为目录骨架，LLM 抽取出的细考点会保存在题目变体里作为子标签。当前一级目录包括：

- `Java基础`
- `Java并发`
- `JVM`
- `Spring`
- `MySQL`
- `Redis`
- `消息队列`
- `计算机网络`
- `操作系统`
- `分布式系统`
- `系统设计`
- `项目经历`
- `算法题`
- `AI/RAG`
- `工程实践`
- `HR/行为面`
- `未分类`

```powershell
python -m catch_knowledge.cli export-obsidian
```

这个命令会基于当前数据库生成 Obsidian 友好的 Markdown 目录结构：

- `knowledge_base/面经/公司名/单篇面经.md`
- `knowledge_base/公司/公司名.md`
- `knowledge_base/面试题/知识点.md`
- `knowledge_base/算法题/算法题.md`
- `knowledge_base/面经知识库.md`

在 Obsidian 中直接选择 `knowledge_base/` 作为 Vault 打开，然后从 `面经知识库.md` 进入即可。

### 同步 Obsidian 手动修改

如果你在 Obsidian 里修改了单篇面经内容，可以把修改同步回数据库：

```powershell
python -m catch_knowledge.cli sync-obsidian
```

第一版只同步 `knowledge_base/面经/**/*.md`，不会读取公司页、面试题页、算法题页这些自动索引页。支持同步的内容包括：

- frontmatter 里的 `company`、`role`、`direction`、`rounds`、`tags`
- `## 面试题`
- `## 知识点`
- `## 摘要`
- `## 原文`
- `## 图片 OCR`

同步依赖单篇面经 frontmatter 里的 `raw_post_id`。如果旧文件没有这个字段，先重新执行一次 `export-obsidian`。

## PostgreSQL 切换方案

现在本机还没装 PostgreSQL 也没关系，项目已经先准备好了切换能力。

建议顺序：

1. 先在本机或服务器装 PostgreSQL
2. 修改 `.env` 里的 `DATABASE_URL`
3. 执行 `init-db`
4. 如需保留旧数据，执行 `migrate-sqlite-to-db`

### PostgreSQL 依赖

```powershell
pip install -e .[postgres]
```

### PostgreSQL 连接串示例

```env
DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/catch_knowledge
```

### 切换步骤

1. 安装 PostgreSQL
2. 新建数据库，例如 `catch_knowledge`
3. 安装 Python 驱动：

```powershell
pip install -e .[postgres]
```

4. 修改 `.env`
5. 初始化表：

```powershell
python -m catch_knowledge.cli init-db
```

6. 如需迁移旧 SQLite 数据：

```powershell
python -m catch_knowledge.cli migrate-sqlite-to-db --sqlite-path ./data/catch_knowledge.db
```

## 火山 OCR 配置

当前使用火山通用文字识别 `OCRNormal`：

- Endpoint: `https://visual.volcengineapi.com`
- Action: `OCRNormal`
- Version: `2020-08-26`
- Region: `cn-north-1`
- Service: `cv`

需要在 `.env` 里填写：

```env
OCR_ENABLED=true
OCR_PROVIDER=volcengine
OCR_DOWNLOAD_TIMEOUT_SECONDS=30
OCR_MAX_IMAGES_PER_POST=9

VOLCENGINE_OCR_AK=
VOLCENGINE_OCR_SK=
VOLCENGINE_OCR_ENDPOINT=https://visual.volcengineapi.com
VOLCENGINE_OCR_REGION=cn-north-1
VOLCENGINE_OCR_SERVICE=cv
VOLCENGINE_OCR_MODE=default
VOLCENGINE_OCR_FILTER_THRESH=80
VOLCENGINE_OCR_HALF_TO_FULL=false
```

## LLM 配置

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_MODEL=deepseek-ai/DeepSeek-V3
LLM_RETRY_COUNT=2
LLM_RETRY_BACKOFF_SECONDS=3
```

## 小红书模式

先启动 MCP：

```powershell
cd E:\vibe_coding\catch_knowledge\xiaohongshu-mcp
go run . -headless=false
```

再回项目根目录：

```powershell
python -m catch_knowledge.cli xhs-mcp-status
python -m catch_knowledge.cli xhs-search
python -m catch_knowledge.cli run-once
```

## 后续计划

### 1. 数据层

- 正式切到 PostgreSQL
- 为后续统计、高频题聚合、Web 查询接口做准备
- 当前本地开发已经支持通过 Docker Compose 启动 PostgreSQL

### 2. Obsidian 知识库

- 将 `knowledge_base/` 作为 Obsidian Vault 打开
- 使用公司页浏览每家公司面经
- 使用知识点页查看相关面试题
- 使用算法题页查看算法题频次和来源
- 使用双链连接公司、知识点和单篇面经

### 3. 知识库增强

- 按公司分类
- 按知识点聚合题目
- 统计高频题
- 统计算法题、场景题、项目题
- 当前已经有第一版题目索引；后续可以在同知识点候选范围内加入 LLM 精修，进一步合并相似问法

### 4. 服务器部署

- 云服务器定时执行抓取
- OCR 和 LLM 自动处理
- 日志、失败重试和监控

## 数据位置

- SQLite 文件：
  [catch_knowledge.db](e:\vibe_coding\catch_knowledge\data\catch_knowledge.db)

- Markdown 知识库：
  [knowledge_base](e:\vibe_coding\catch_knowledge\knowledge_base)
