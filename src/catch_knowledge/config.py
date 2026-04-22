import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_env: str = "dev"
    database_url: str
    openai_api_key: str = ""
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4.1-mini"
    knowledge_base_dir: Path = Path("./knowledge_base")
    raw_archive_dir: Path = Path("./data/raw")
    image_cache_dir: Path = Path("./data/images")
    web_upload_dir: Path = Path("./data/web_uploads")

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
    xhs_request_timeout_seconds: int = 120
    xhs_retry_count: int = 2
    xhs_retry_backoff_seconds: float = 3.0
    xhs_min_delay_seconds: float = 2.0
    xhs_max_delay_seconds: float = 5.0
    xhs_login_qrcode_path: Path = Path("./data/xhs_login_qrcode.png")

    ocr_enabled: bool = False
    ocr_provider: str = "volcengine"
    ocr_download_timeout_seconds: int = 30
    ocr_max_images_per_post: int = 9
    volcengine_ocr_ak: str = ""
    volcengine_ocr_sk: str = ""
    volcengine_ocr_endpoint: str = "https://visual.volcengineapi.com"
    volcengine_ocr_region: str = "cn-north-1"
    volcengine_ocr_service: str = "cv"
    volcengine_ocr_scene: str = "general"
    volcengine_ocr_mode: str = "default"
    volcengine_ocr_filter_thresh: str = "80"
    volcengine_ocr_half_to_full: bool = False

    llm_retry_count: int = 2
    llm_retry_backoff_seconds: float = 3.0
    schedule_cron: str = "0 0 * * *"
    timezone: str = "Asia/Shanghai"

    @field_validator("nowcoder_seed_urls", "xhs_keywords", mode="before")
    @classmethod
    def parse_list_values(cls, value):
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
    settings.image_cache_dir.mkdir(parents=True, exist_ok=True)
    settings.web_upload_dir.mkdir(parents=True, exist_ok=True)
    settings.nowcoder_storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    settings.xhs_login_qrcode_path.parent.mkdir(parents=True, exist_ok=True)
    return settings
