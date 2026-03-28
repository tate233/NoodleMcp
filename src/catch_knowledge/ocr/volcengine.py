from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode, urlparse

import httpx

from catch_knowledge.config import Settings
from catch_knowledge.domain import CollectedPost


class VolcengineOCRProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.Client(timeout=settings.ocr_download_timeout_seconds, follow_redirects=True)

    def enrich_post(self, post: CollectedPost) -> CollectedPost:
        if not self.settings.ocr_enabled or self.settings.ocr_provider != "volcengine":
            return post
        if not post.image_urls:
            return post

        errors: List[str] = []
        ocr_chunks: List[str] = []
        cached_paths: List[str] = []

        for index, image_url in enumerate(post.image_urls[: self.settings.ocr_max_images_per_post], start=1):
            try:
                file_path = self._download_image(post, image_url, index)
                cached_paths.append(str(file_path))
                text = self._ocr_image(file_path)
                if text:
                    ocr_chunks.append(text)
            except Exception as exc:
                errors.append(f"{image_url}: {exc}")

        raw_image_text = "\n\n".join(chunk for chunk in ocr_chunks if chunk.strip()) or None
        merged_text = self._merge_text(post.raw_source_text, raw_image_text)

        metadata = dict(post.metadata_json or {})
        metadata["image_urls"] = post.image_urls
        metadata["cached_image_paths"] = cached_paths
        if raw_image_text:
            metadata["ocr_text"] = raw_image_text
        if errors:
            metadata["ocr_errors"] = errors

        return CollectedPost(
            platform=post.platform,
            post_id=post.post_id,
            url=post.url,
            title=post.title,
            author_name=post.author_name,
            published_at=post.published_at,
            raw_html=post.raw_html,
            raw_source_text=post.raw_source_text,
            raw_image_text=raw_image_text,
            raw_text=merged_text,
            image_urls=post.image_urls,
            metadata_json=metadata,
        )

    def _download_image(self, post: CollectedPost, image_url: str, index: int) -> Path:
        normalized_url = self._normalize_image_url(image_url)
        response = self._client.get(normalized_url)
        response.raise_for_status()
        suffix = self._guess_suffix(normalized_url, response.headers.get("content-type"))
        digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:12]
        filename = f"{post.platform}_{post.post_id}_{index}_{digest}{suffix}"
        file_path = self.settings.image_cache_dir / filename
        file_path.write_bytes(response.content)
        return file_path

    def _ocr_image(self, file_path: Path) -> Optional[str]:
        if not (self.settings.volcengine_ocr_ak and self.settings.volcengine_ocr_sk):
            raise RuntimeError("Volcengine OCR is enabled but AK/SK is not fully configured.")

        image_base64 = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        body_data = {
            "image_base64": image_base64,
            "mode": self.settings.volcengine_ocr_mode,
            "filter_thresh": self.settings.volcengine_ocr_filter_thresh,
            "half_to_full": "true" if self.settings.volcengine_ocr_half_to_full else "false",
        }
        body = urlencode(body_data)
        response = self._signed_post(body)
        payload = response.json()

        if payload.get("code") != 10000:
            raise RuntimeError(f"Volcengine OCR failed: {payload}")

        lines = payload.get("data", {}).get("line_texts") or []
        if not isinstance(lines, list):
            return None
        text = "\n".join(str(line).strip() for line in lines if str(line).strip())
        return text or None

    def _signed_post(self, body: str) -> httpx.Response:
        endpoint = self.settings.volcengine_ocr_endpoint.rstrip("/")
        parsed = urlparse(endpoint)
        host = parsed.netloc
        canonical_uri = parsed.path or "/"
        query = "Action=OCRNormal&Version=2020-08-26"
        now = datetime.now(timezone.utc)
        request_date = now.strftime("%Y%m%dT%H%M%SZ")
        short_date = now.strftime("%Y%m%d")

        canonical_headers = (
            f"content-type:application/x-www-form-urlencoded\n"
            f"host:{host}\n"
            f"x-date:{request_date}\n"
        )
        signed_headers = "content-type;host;x-date"
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                canonical_uri,
                query,
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )

        region = self.settings.volcengine_ocr_region
        service = self.settings.volcengine_ocr_service
        credential_scope = f"{short_date}/{region}/{service}/request"
        string_to_sign = "\n".join(
            [
                "HMAC-SHA256",
                request_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signature = self._calculate_signature(
            self.settings.volcengine_ocr_sk,
            short_date,
            region,
            service,
            string_to_sign,
        )

        authorization = (
            "HMAC-SHA256 "
            f"Credential={self.settings.volcengine_ocr_ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Host": host,
            "X-Date": request_date,
            "Authorization": authorization,
        }
        response = self._client.post(f"{endpoint}?{query}", headers=headers, content=body)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Volcengine OCR HTTP {response.status_code}: {response.text}"
            )
        return response

    @staticmethod
    def _calculate_signature(secret_key: str, short_date: str, region: str, service: str, string_to_sign: str) -> str:
        def sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        k_date = sign(secret_key.encode("utf-8"), short_date)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "request")
        return hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _merge_text(source_text: Optional[str], image_text: Optional[str]) -> Optional[str]:
        parts = []
        if source_text:
            parts.append(source_text.strip())
        if image_text:
            parts.append("图片OCR:\n" + image_text.strip())
        merged = "\n\n".join(part for part in parts if part)
        return merged or None

    @staticmethod
    def _normalize_image_url(image_url: str) -> str:
        if image_url.startswith("http://"):
            return "https://" + image_url[len("http://") :]
        return image_url

    @staticmethod
    def _guess_suffix(image_url: str, content_type: Optional[str]) -> str:
        url_suffix = Path(image_url.split("?", 1)[0]).suffix.lower()
        if url_suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            return url_suffix
        content_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
        }
        return content_map.get((content_type or "").split(";")[0].strip().lower(), ".jpg")
