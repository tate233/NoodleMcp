from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from catch_knowledge.config import get_settings
from catch_knowledge.db import create_session_factory, create_tables
from catch_knowledge.db.models import KBDocument, PostAnalysis, RawPost
from catch_knowledge.pipeline import build_question_index, export_obsidian_vault, import_manual_note

settings = get_settings()
create_tables(settings)
session_factory = create_session_factory(settings)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Catch Knowledge Console")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    records = _fetch_recent_records(limit=30)
    message = request.query_params.get("message")
    message_type = request.query_params.get("type", "info")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "records": records,
            "message": message,
            "message_type": message_type,
        },
    )


@app.post("/manual-import")
async def manual_import(
    request: Request,
    title: str = Form(default=""),
    source_url: str = Form(default=""),
    author: str = Form(default=""),
    text: str = Form(default=""),
    files: List[UploadFile] | None = None,
):
    upload_paths = await _persist_uploads(files or [])
    text_file = next((path for path in upload_paths if path.suffix.lower() in {".txt", ".md"}), None)
    image_files = [path for path in upload_paths if path.suffix.lower() not in {".txt", ".md"}]

    result = import_manual_note(
        settings,
        title=title or None,
        text=text or None,
        text_file=text_file,
        image_files=image_files,
        source_url=source_url or None,
        author_name=author or None,
    )
    raw_post_id = result.get("raw_post_id")
    if raw_post_id:
        return RedirectResponse(url=f"/posts/{raw_post_id}?message=导入完成&type=success", status_code=303)
    return RedirectResponse(url="/?message=导入完成，但未找到记录&type=warning", status_code=303)


@app.post("/actions/build-index")
def action_build_index():
    build_question_index(settings)
    return RedirectResponse(url="/?message=题目索引已重建&type=success", status_code=303)


@app.post("/actions/export-obsidian")
def action_export_obsidian():
    export_obsidian_vault(settings)
    return RedirectResponse(url="/?message=Obsidian 已重新导出&type=success", status_code=303)


@app.get("/posts/{raw_post_id}", response_class=HTMLResponse)
def post_detail(request: Request, raw_post_id: int):
    payload = _fetch_post_detail(raw_post_id)
    return templates.TemplateResponse(
        "post_detail.html",
        {
            "request": request,
            "record": payload,
            "message": request.query_params.get("message"),
            "message_type": request.query_params.get("type", "info"),
        },
    )


def _fetch_recent_records(limit: int) -> List[dict]:
    with session_factory() as session:
        session: Session
        rows = (
            session.query(RawPost, PostAnalysis, KBDocument)
            .outerjoin(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .outerjoin(KBDocument, KBDocument.raw_post_id == RawPost.id)
            .order_by(RawPost.crawled_at.desc(), RawPost.id.desc())
            .limit(limit)
            .all()
        )
        return [_serialize_record(raw_post, analysis, kb_document) for raw_post, analysis, kb_document in rows]


def _fetch_post_detail(raw_post_id: int) -> Optional[dict]:
    with session_factory() as session:
        session: Session
        row = (
            session.query(RawPost, PostAnalysis, KBDocument)
            .outerjoin(PostAnalysis, PostAnalysis.raw_post_id == RawPost.id)
            .outerjoin(KBDocument, KBDocument.raw_post_id == RawPost.id)
            .filter(RawPost.id == raw_post_id)
            .one_or_none()
        )
        if row is None:
            return None
        return _serialize_record(*row)


def _serialize_record(raw_post: RawPost, analysis: PostAnalysis | None, kb_document: KBDocument | None) -> dict:
    return {
        "raw_post_id": raw_post.id,
        "platform": raw_post.platform,
        "post_id": raw_post.post_id,
        "title": raw_post.title or "未命名面经",
        "url": raw_post.url,
        "author_name": raw_post.author_name,
        "status": raw_post.status,
        "published_at": _format_datetime(raw_post.published_at),
        "crawled_at": _format_datetime(raw_post.crawled_at),
        "raw_source_text": raw_post.raw_source_text or "",
        "raw_image_text": raw_post.raw_image_text or "",
        "raw_text": raw_post.raw_text or "",
        "image_urls": raw_post.image_urls or [],
        "metadata_json": raw_post.metadata_json or {},
        "analysis": {
            "is_interview_experience": analysis.is_interview_experience if analysis else False,
            "company": analysis.company if analysis else "",
            "job_role": analysis.job_role if analysis else "",
            "job_direction": analysis.job_direction if analysis else "",
            "interview_rounds": analysis.interview_rounds or [] if analysis else [],
            "tags": analysis.tags or [] if analysis else [],
            "interview_questions": analysis.interview_questions or [] if analysis else [],
            "question_points": analysis.question_points or [] if analysis else [],
            "summary": analysis.summary if analysis else "",
            "difficulty": analysis.difficulty if analysis else "",
        },
        "markdown_path": kb_document.markdown_path if kb_document else "",
    }


async def _persist_uploads(files: List[UploadFile]) -> List[Path]:
    if not files:
        return []

    target_dir = settings.web_upload_dir / datetime.now().strftime("%Y%m%d_%H%M%S") / uuid4().hex[:8]
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []

    for upload in files:
        if not upload.filename:
            continue
        filename = Path(upload.filename).name
        target = target_dir / filename
        data = await upload.read()
        target.write_bytes(data)
        paths.append(target)
    return paths


def _format_datetime(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)
