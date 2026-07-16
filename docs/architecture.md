# Catch Knowledge Architecture

## 目标

Catch Knowledge 是一个面经与 AI 应用开发知识采集系统。它从小红书、QQ 私聊、手动上传等入口收集原始材料，经过 OCR、LLM 结构化分析、题目归并和 Markdown 导出，最终生成可以直接用 Obsidian 打开的个人知识库。

当前主要关注这些内容：

- 后端面经
- Agent 开发
- 大模型应用开发
- AI 应用开发
- RAG、工具调用、模型工程、项目经验等相关主题

## 总体链路

```text
内容入口
  -> raw_posts 原始数据入库
  -> OCR 补全文字
  -> LLM 结构化分析
  -> post_analysis 分析结果入库
  -> canonical_questions 题目归并与分类
  -> knowledge_base Markdown 导出
```

## 内容入口

### 小红书 MCP

主入口由 `SOURCE_PLATFORM=xiaohongshu_mcp` 控制。

运行时流程：

1. `XiaohongshuMCPCollector` 调用 `XHS_MCP_BASE_URL`。
2. MCP 服务负责保持小红书登录态。
3. collector 按 `XHS_KEYWORDS` 逐个关键词搜索。
4. 每条搜索结果再请求详情接口。
5. 标题、正文、评论、图片链接等被整理成 `CollectedPost`。

关键词配置在 `.env`：

```env
XHS_KEYWORDS=后端 面经,Agent 面经,大模型应用开发 面经,AI应用开发 面经
```

这些值会被按逗号解析成多个关键词，逐个抓取。

### QQ / NapCat

QQ 入口由 `qq-adapter` 提供。

```text
NapCat 私聊消息
  -> POST /qq/webhook 或 WebSocket /qq/ws
  -> QQ adapter 提取文本和图片
  -> 转发到 Web API
  -> 进入同一套 OCR / LLM / 导出链路
```

推荐只让 NapCat、QQ adapter、Web API 在服务器内网或 `127.0.0.1` 通信。

### 手动上传

手动入口有两种：

- Web 控制台上传文本或图片
- CLI 执行 `manual-import`

它们都会构造 `CollectedPost`，然后进入统一处理链路。

## 数据处理

### 1. 原始数据入库

`upsert_raw_post` 根据 `platform + post_id` 去重写入 `raw_posts`。

主要字段：

- `raw_source_text`：原帖文字
- `raw_image_text`：OCR 识别出的图片文字
- `raw_text`：给 LLM 使用的合并文本
- `image_urls`：图片 URL 或上传图片路径
- `metadata_json`：抓取详情、错误信息、重试状态等
- `status`：采集、分析、fallback、失败等状态

### 2. OCR

如果开启：

```env
OCR_ENABLED=true
OCR_PROVIDER=volcengine
```

系统会下载图片并调用火山 OCR，把结果写入 `raw_image_text`，再合并到 `raw_text`。

### 3. LLM 结构化分析

`LLMAnalyzer` 输出结构化字段：

- `content_type`
- `is_interview_experience`
- `company`
- `job_role`
- `job_direction`
- `interview_rounds`
- `tags`
- `interview_questions`
- `question_points`
- `summary`
- `difficulty`

结果写入 `post_analysis`。

如果主模型失败，会按配置重试，并可使用备用模型；仍失败时会写入 fallback 信息，后续可用 retry queue 补跑。

### 4. 题目归并与分类

`QuestionIndexBuilder` 把 LLM 抽取的题目写入 `canonical_questions`。

它使用固定一级 taxonomy，避免知识库目录被 LLM 随机命名污染。细粒度考点会保存在 `variants[].subtopics` 里。

`canonical_question_sources` 是题目索引的反向来源表，记录每个 canonical question 来自哪条 `raw_posts`、原始题目文本和细考点。删除或重分析单条面经时，系统会先按 `raw_post_id` 命中这张表，再精准刷新对应 canonical question，避免遍历所有 `canonical_questions`。

当前重点分类包括：

- 后端基础：MySQL、Redis、消息队列、分布式系统、Java、Spring、网络、操作系统、系统设计
- AI 应用：AI/RAG、Agent开发、LLM应用工程
- 其他：项目经历、HR/行为面、工程实践
- 算法题：单独进入 `kind=algorithm`

## 知识库导出

`MarkdownExporter` 负责生成 Obsidian Vault。

当前目录结构：

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
  Agent/
    Agent.md
    Agent开发.md
    AI_RAG.md
    LLM应用工程.md
  面经知识库.md
```

其中：

- `面经/` 保存完整面经原文和分析结果。
- `公司/` 按公司聚合面经。
- `面试题/` 按一级知识点聚合题目。
- `算法题/` 单独统计手撕题和算法题。
- `Agent/` 聚合 Agent、RAG、大模型应用、AI 应用开发相关题目。
- `面经知识库.md` 是首页索引。

## 增量更新

日常抓取不会每次全量重建。

`run-once` 的增量流程：

1. 抓取当前关键词结果。
2. 新帖或更新帖写入 `raw_posts`。
3. 分析本次影响的帖子。
4. 同步这些帖子的 canonical question 记录。
5. 只刷新受影响的单篇面经、公司页、知识点页、算法题页、Agent 页和首页。

需要彻底重算时再手动执行：

```bash
python -m catch_knowledge.cli build-question-index
python -m catch_knowledge.cli export-obsidian
```

## 运行入口

常用命令：

```bash
python -m catch_knowledge.cli init-db
python -m catch_knowledge.cli xhs-mcp-status
python -m catch_knowledge.cli xhs-mcp-qrcode
python -m catch_knowledge.cli xhs-search
python -m catch_knowledge.cli run-once
python -m catch_knowledge.cli schedule
python -m catch_knowledge.cli web --host 127.0.0.1 --port 8000
python -m catch_knowledge.cli qq-adapter --host 127.0.0.1 --port 8090 --ingest-base-url http://127.0.0.1:8000
```

## 服务部署关系

推荐服务器上长期运行：

```text
PostgreSQL
Catch Knowledge Web
QQ adapter
NapCat
xiaohongshu-mcp
schedule 或 crontab
```

端口建议：

```text
PostgreSQL:       127.0.0.1:5432
Web:              127.0.0.1:8000
QQ adapter:       127.0.0.1:8090
NapCat HTTP API:  127.0.0.1:3000
xiaohongshu-mcp:  127.0.0.1:18060
```

除 Web 控制台外，不建议把任何内部服务暴露到公网。个人使用时优先通过 SSH 隧道访问 Web。

## 小红书登录

小红书登录由 `xiaohongshu-mcp` 维护。

无桌面服务器上可以这样取二维码：

```bash
python -m catch_knowledge.cli xhs-mcp-qrcode
```

命令会把二维码保存到：

```text
data/xhs_login_qrcode.png
```

把图片拉回本地扫码，扫码后执行：

```bash
python -m catch_knowledge.cli xhs-mcp-status
```

返回已登录后，`xhs-search` 和 `run-once` 才能正常抓取。

## 当前维护重点

- 关键词从单一“后端面经”扩展为后端、Agent、大模型应用开发、AI 应用开发。
- 知识库新增 `Agent/` 目录，方便集中复习 AI 应用相关面试题。
- `面试题/` 仍保留完整一级 taxonomy，`Agent/` 是面向 AI 应用方向的聚合视图。
- 后续如果新方向增多，优先扩展 `QuestionIndexBuilder.taxonomy`，再决定是否新增独立知识库目录。
