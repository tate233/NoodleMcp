# Architecture

## Goal

Run a daily Nowcoder collection job, extract structured interview information with an LLM, and export organized Markdown notes for a personal interview knowledge base.

## Pipeline

1. `scheduler`
   Starts a daily job from a cron expression.
2. `collector`
   Fetches Nowcoder seed pages and then resolves candidate detail links.
3. `storage`
   Upserts raw posts into PostgreSQL and computes a content hash.
4. `llm analyzer`
   Converts noisy forum text into structured interview fields.
5. `kb exporter`
   Writes Markdown notes into a month-based directory tree.

## Database Tables

### `raw_posts`

- Stores the original crawl result and crawl metadata.
- Unique key: `platform + post_id`

### `post_analysis`

- Stores structured extraction results from the LLM.
- One-to-one with `raw_posts`

### `kb_documents`

- Tracks exported Markdown files.
- One-to-one with `raw_posts`

## Why This Split

- Crawling stays deterministic and cheap.
- LLM usage focuses on normalization and summarization.
- Markdown output keeps the knowledge base portable.
- PostgreSQL leaves room for future full-text and vector search.

## Expected Next Iterations

- Replace placeholder selectors in the Nowcoder collector with verified selectors or API calls.
- Archive original HTML into `data/raw`.
- Add a retry strategy and structured logging.
- Add embeddings with `pgvector`.
- Add a small query UI or retrieval API.
