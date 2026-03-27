import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    database_url: str
    openai_api_key: str = ""
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4.1-mini"
    knowledge_base_dir: Path = Path("./knowledge_base")
    raw_archive_dir: Path = Path("./data/raw")
    source_platform: str = "xiaohongshu_mcp"
    nowcoder_seed_urls: List[str] = Field(default_factory=list)
    nowcoder_request_timeout_seconds: int = 20
    nowcoder_use_playwright: bool = False
    nowcoder_browser_headless: bool = True
    nowcoder_storage_state_path: Path = Path("./data/nowcoder_storage_state.json")
    xhs_mcp_base_url: str = "http://127.0.0.1:18060"
    xhs_keywords: List[str] = Field(default_factory=list)
    xhs_search_sort_by: str = "最新"
    xhs_search_note_type: str = "不限"
    xhs_search_publish_time: str = "一天内"
    xhs_search_scope: str = "不限"
    xhs_search_location: str = "不限"
    xhs_fetch_comments: bool = False
    xhs_max_results_per_keyword: int = 10
    xhs_login_qrcode_path: Path = Path("./data/xhs_login_qrcode.png")
    schedule_cron: str = "0 0 * * *"
    timezone: str = "Asia/Shanghai"

    @field_validator("nowcoder_seed_urls", mode="before")
    @classmethod
    def parse_seed_urls(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("xhs_keywords", mode="before")
    @classmethod
    def parse_xhs_keywords(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.knowledge_base_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_archive_dir.mkdir(parents=True, exist_ok=True)
    settings.nowcoder_storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.xhs_login_qrcode_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
