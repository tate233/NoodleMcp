from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from catch_knowledge.config import Settings
from catch_knowledge.domain import CollectedPost


@dataclass
class ManualImportRequest:
    title: Optional[str]
    text: Optional[str]
    text_file: Optional[Path]
    image_files: List[Path]
    source_url: Optional[str] = None
    author_name: Optional[str] = None


def build_manual_post(settings: Settings, request: ManualImportRequest) -> CollectedPost:
    text_content = _resolve_text(request.text, request.text_file)
    title = _resolve_title(request.title, request.text_file, text_content, request.image_files)
    if not text_content and not request.image_files:
        raise ValueError("Manual import requires text or at least one image.")

    archive_root = settings.raw_archive_dir / "manual_uploads"
    archive_root.mkdir(parents=True, exist_ok=True)

    post_id = _build_post_id(title, text_content, request.image_files)
    record_dir = archive_root / post_id
    record_dir.mkdir(parents=True, exist_ok=True)

    archived_text_path = None
    if request.text_file and request.text_file.exists():
        archived_text_path = record_dir / request.text_file.name
        shutil.copy2(request.text_file, archived_text_path)
    elif text_content:
        archived_text_path = record_dir / "note.txt"
        archived_text_path.write_text(text_content, encoding="utf-8")

    archived_images = []
    for image_path in request.image_files:
        if not image_path.exists():
            raise FileNotFoundError(f"Image file does not exist: {image_path}")
        target = record_dir / image_path.name
        shutil.copy2(image_path, target)
        archived_images.append(str(target.resolve()))

    metadata: Dict = {
        "source_type": "manual_upload",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "original_text_file": str(request.text_file.resolve()) if request.text_file else None,
        "original_image_files": [str(path.resolve()) for path in request.image_files],
        "archived_text_file": str(archived_text_path.resolve()) if archived_text_path else None,
        "archived_image_files": archived_images,
    }

    return CollectedPost(
        platform="manual_upload",
        post_id=post_id,
        url=request.source_url or f"manual://{post_id}",
        title=title,
        author_name=request.author_name,
        published_at=datetime.now(timezone.utc),
        raw_html=None,
        raw_source_text=text_content,
        raw_image_text=None,
        raw_text=text_content,
        image_urls=archived_images,
        metadata_json=metadata,
    )


def _build_post_id(title: str, text_content: Optional[str], image_files: List[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(title.encode("utf-8"))
    digest.update((text_content or "").encode("utf-8"))
    for image_path in image_files:
        digest.update(str(image_path.resolve()).encode("utf-8"))
        if image_path.exists():
            digest.update(str(image_path.stat().st_size).encode("utf-8"))
    return f"manual_{digest.hexdigest()[:24]}"


def _resolve_title(
    explicit_title: Optional[str],
    text_file: Optional[Path],
    text_content: Optional[str],
    image_files: List[Path],
) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    if text_file is not None:
        return text_file.stem
    if text_content:
        first_line = next((line.strip() for line in text_content.splitlines() if line.strip()), "")
        if first_line:
            return first_line[:80]
    if image_files:
        return image_files[0].stem
    return "手动上传面经"


def _resolve_text(inline_text: Optional[str], text_file: Optional[Path]) -> Optional[str]:
    if inline_text and inline_text.strip():
        return inline_text.strip()
    if text_file is None:
        return None
    if not text_file.exists():
        raise FileNotFoundError(f"Text file does not exist: {text_file}")

    data = text_file.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore").strip()
