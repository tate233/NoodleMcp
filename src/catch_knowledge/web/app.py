from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from catch_knowledge.config import get_settings
from catch_knowledge.db import create_session_factory, create_tables
from catch_knowledge.db.models import KBDocument, PostAnalysis, RawPost
from catch_knowledge.exporters import MarkdownExporter
from catch_knowledge.llm import LLMAnalyzer
from catch_knowledge.pipeline import (
    build_question_index,
    export_obsidian_vault,
    import_manual_note,
    reanalyze_single_post as pipeline_reanalyze_single_post,
)
from catch_knowledge.storage import save_analysis

settings = get_settings()
create_tables(settings)
session_factory = create_session_factory(settings)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Catch Knowledge Console")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class IngestTextRequest(BaseModel):
    title: Optional[str] = None
    text: str = Field(default="")
    source_url: Optional[str] = None
    author: Optional[str] = None
    source: Optional[str] = "api"
    sender: Optional[str] = None


@app.get("/api/health")
def api_health():
    return {"ok": True, "service": "catch-knowledge-web-api"}


@app.post("/api/ingest/text")
def api_ingest_text(payload: IngestTextRequest):
    try:
        result = import_manual_note(
            settings,
            title=_normalize_form_text(payload.title or "") or None,
            text=_normalize_form_text(payload.text or "") or None,
            text_file=None,
            image_files=[],
            source_url=_normalize_form_text(payload.source_url or "") or None,
            author_name=_normalize_form_text(payload.author or "") or None,
        )
        return _build_ingest_response(result, source=payload.source, sender=payload.sender)
    except Exception as exc:
        return {
            "ok": False,
            "source": payload.source or "api",
            "sender": payload.sender or "",
            "error": repr(exc),
        }


@app.post("/api/ingest/message")
async def api_ingest_message(
    title: str = Form(default=""),
    text: str = Form(default=""),
    source_url: str = Form(default=""),
    author: str = Form(default=""),
    source: str = Form(default="api"),
    sender: str = Form(default=""),
    files: Optional[List[UploadFile]] = None,
):
    try:
        upload_paths = await _persist_uploads(files or [])
        text_file = next((path for path in upload_paths if path.suffix.lower() in {".txt", ".md"}), None)
        image_files = [path for path in upload_paths if path.suffix.lower() not in {".txt", ".md"}]

        result = import_manual_note(
            settings,
            title=_normalize_form_text(title) or None,
            text=_normalize_form_text(text) or None,
            text_file=text_file,
            image_files=image_files,
            source_url=_normalize_form_text(source_url) or None,
            author_name=_normalize_form_text(author) or None,
        )
        return _build_ingest_response(result, source=source or "api", sender=sender or None)
    except Exception as exc:
        return {
            "ok": False,
            "source": source or "api",
            "sender": sender or "",
            "error": repr(exc),
        }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    records = _fetch_recent_records(limit=30)
    message = request.query_params.get("message")
    message_type = request.query_params.get("type", "info")
    selected_id = request.query_params.get("selected")
    selected_record = None
    if selected_id and str(selected_id).isdigit():
        selected_record = _fetch_post_detail(int(selected_id))
    if selected_record is None and records:
        selected_record = _fetch_post_detail(records[0]["raw_post_id"])
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "records": records,
            "selected_record": selected_record,
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
    files: Optional[List[UploadFile]] = None,
):
    try:
        text = _normalize_form_text(text)
        title = _normalize_form_text(title)
        source_url = _normalize_form_text(source_url)
        author = _normalize_form_text(author)
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
            return _redirect("/", "导入完成", "success", selected=raw_post_id)
        return _redirect("/", "导入完成，但未找到记录", "warning")
    except Exception as exc:
        return _redirect("/", f"整理失败：{exc}", "error")


@app.post("/actions/build-index")
def action_build_index():
    try:
        build_question_index(settings)
        return _redirect("/", "题目索引已重建", "success")
    except Exception as exc:
        return _redirect("/", f"重建索引失败：{exc}", "error")


@app.post("/actions/export-obsidian")
def action_export_obsidian():
    try:
        export_obsidian_vault(settings)
        return _redirect("/", "Obsidian 已重新导出", "success")
    except Exception as exc:
        return _redirect("/", f"导出失败：{exc}", "error")


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


@app.post("/posts/{raw_post_id}/reanalyze")
def action_reanalyze_post(raw_post_id: int):
    try:
        result = _reanalyze_single_post(raw_post_id)
        _refresh_knowledge_outputs()
        if result.get("status") == "analysis_fallback":
            _schedule_background_reanalyze(raw_post_id)
            detail = result.get("llm_error") or "LLM 本次仍未返回结构化结果"
            return _redirect(
                f"/posts/{raw_post_id}",
                f"这条记录已重试，但本次仍走了 fallback：{detail}。已加入后台补跑。",
                "warning",
            )
        if result.get("status") == "analysis_failed":
            detail = result.get("llm_error") or "分析阶段失败"
            return _redirect(f"/posts/{raw_post_id}", f"重新分析失败：{detail}", "error")
        return _redirect(f"/posts/{raw_post_id}", "该记录已重新分析并写回数据库", "success")
    except Exception as exc:
        return _redirect(f"/posts/{raw_post_id}", f"重新分析失败：{exc}", "error")


@app.post("/posts/{raw_post_id}/content-type")
def action_update_content_type(raw_post_id: int, content_type: str = Form(...)):
    try:
        _update_single_content_type(raw_post_id, content_type)
        _refresh_knowledge_outputs()
        return _redirect(f"/posts/{raw_post_id}", "内容类型已更新", "success")
    except Exception as exc:
        return _redirect(f"/posts/{raw_post_id}", f"更新内容类型失败：{exc}", "error")


@app.post("/posts/{raw_post_id}/edit")
def action_edit_post(
    raw_post_id: int,
    title: str = Form(default=""),
    raw_source_text: str = Form(default=""),
    company: str = Form(default=""),
    job_role: str = Form(default=""),
    job_direction: str = Form(default=""),
    summary: str = Form(default=""),
    interview_questions: str = Form(default=""),
    question_points: str = Form(default=""),
    content_type: str = Form(default="interview_note"),
):
    try:
        _edit_single_post(
            raw_post_id=raw_post_id,
            title=title,
            raw_source_text=raw_source_text,
            company=company,
            job_role=job_role,
            job_direction=job_direction,
            summary=summary,
            interview_questions=interview_questions,
            question_points=question_points,
            content_type=content_type,
        )
        _refresh_knowledge_outputs()
        return _redirect(f"/posts/{raw_post_id}", "内容已更新", "success")
    except Exception as exc:
        return _redirect(f"/posts/{raw_post_id}", f"保存修改失败：{exc}", "error")


@app.post("/posts/{raw_post_id}/delete")
def action_delete_post(raw_post_id: int):
    try:
        _delete_single_post(raw_post_id)
        _refresh_knowledge_outputs()
        return _redirect("/", "面经已删除", "success")
    except Exception as exc:
        return _redirect(f"/posts/{raw_post_id}", f"删除失败：{exc}", "error")


def _redirect(path: str, message: str, message_type: str = "info", **params):
    query = {"message": message, "type": message_type}
    query.update({key: value for key, value in params.items() if value is not None})
    return RedirectResponse(url=f"{path}?{urlencode(query)}", status_code=303)


def _build_ingest_response(result: dict, *, source: Optional[str], sender: Optional[str]) -> dict:
    raw_post_id = result.get("raw_post_id")
    record = _fetch_post_detail(int(raw_post_id)) if raw_post_id else None
    return {
        "ok": True,
        "source": source or "api",
        "sender": sender or "",
        "result": result,
        "record": {
            "raw_post_id": record["raw_post_id"] if record else raw_post_id,
            "title": record["title"] if record else "",
            "status": record["status"] if record else "",
            "content_type": (record.get("analysis") or {}).get("content_type", "") if record else "",
            "company": (record.get("analysis") or {}).get("company", "") if record else "",
            "job_role": (record.get("analysis") or {}).get("job_role", "") if record else "",
            "interview_questions": (record.get("analysis") or {}).get("interview_questions", []) if record else [],
            "question_points": (record.get("analysis") or {}).get("question_points", []) if record else [],
            "summary": (record.get("analysis") or {}).get("summary", "") if record else "",
        },
    }


def _refresh_knowledge_outputs() -> None:
    build_question_index(settings)
    export_obsidian_vault(settings)


def _schedule_background_reanalyze(raw_post_id: int, attempts: int = 2, delay_seconds: int = 6) -> None:
    def _worker() -> None:
        for _ in range(max(1, attempts)):
            try:
                result = pipeline_reanalyze_single_post(settings, raw_post_id)
                if result.get("status") != "analysis_fallback":
                    _refresh_knowledge_outputs()
                    return
            except Exception:
                return
            threading.Event().wait(delay_seconds)

    thread = threading.Thread(target=_worker, name=f"ck-reanalyze-{raw_post_id}", daemon=True)
    thread.start()


def _reanalyze_single_post(raw_post_id: int) -> dict:
    result = pipeline_reanalyze_single_post(settings, raw_post_id)
    with session_factory() as session:
        session: Session
        analysis = session.query(PostAnalysis).filter(PostAnalysis.raw_post_id == raw_post_id).one_or_none()
        normalized = dict((analysis.normalized_json or {}) if analysis else {})
        result["llm_error"] = normalized.get("llm_error")
        result["llm_fallback"] = normalized.get("llm_fallback", False)
    return result


def _update_single_content_type(raw_post_id: int, content_type: str) -> None:
    allowed = {"interview_note", "knowledge_snippet", "algorithm_snippet", "noise"}
    content_type = str(content_type or "").strip()
    if content_type not in allowed:
        raise ValueError("不支持的内容类型")

    exporter = MarkdownExporter(settings)
    with session_factory() as session:
        session: Session
        raw_post = session.get(RawPost, raw_post_id)
        if raw_post is None:
            raise ValueError("记录不存在")

        analysis = session.query(PostAnalysis).filter(PostAnalysis.raw_post_id == raw_post.id).one_or_none()
        if analysis is None:
            raise ValueError("这条记录还没有结构化分析结果，请先重新分析")

        analysis.content_type = content_type
        normalized = dict(analysis.normalized_json or {})
        normalized["content_type"] = content_type
        analysis.normalized_json = normalized

        kb_document = session.query(KBDocument).filter(KBDocument.raw_post_id == raw_post.id).one_or_none()
        if analysis.is_interview_experience and content_type == "interview_note":
            title, path = exporter.export(raw_post, analysis)
            if kb_document:
                kb_document.doc_title = title
                kb_document.markdown_path = str(path)
            else:
                session.add(KBDocument(raw_post_id=raw_post.id, doc_title=title, markdown_path=str(path)))
        elif kb_document:
            session.delete(kb_document)

        session.commit()


def _edit_single_post(
    *,
    raw_post_id: int,
    title: str,
    raw_source_text: str,
    company: str,
    job_role: str,
    job_direction: str,
    summary: str,
    interview_questions: str,
    question_points: str,
    content_type: str,
) -> None:
    allowed = {"interview_note", "knowledge_snippet", "algorithm_snippet", "noise"}
    content_type = str(content_type or "").strip() or "interview_note"
    if content_type not in allowed:
        raise ValueError("不支持的内容类型")

    exporter = MarkdownExporter(settings)
    with session_factory() as session:
        session: Session
        raw_post = session.get(RawPost, raw_post_id)
        if raw_post is None:
            raise ValueError("记录不存在")

        analysis = session.query(PostAnalysis).filter(PostAnalysis.raw_post_id == raw_post.id).one_or_none()
        if analysis is None:
            raise ValueError("这条记录还没有结构化分析结果，请先重新分析")

        raw_post.title = _normalize_form_text(title).strip() or raw_post.title
        new_source_text = _normalize_form_text(raw_source_text).strip()
        if new_source_text:
            raw_post.raw_source_text = new_source_text
            raw_post.raw_text = "\n\n".join(part for part in [new_source_text, raw_post.raw_image_text or ""] if part).strip()

        analysis.company = company.strip() or None
        analysis.job_role = job_role.strip() or None
        analysis.job_direction = job_direction.strip() or None
        analysis.summary = summary.strip() or None
        analysis.interview_questions = _parse_multiline_list(interview_questions)
        analysis.question_points = _parse_multiline_list(question_points)
        analysis.content_type = content_type
        analysis.is_interview_experience = content_type != "noise"

        normalized = dict(analysis.normalized_json or {})
        normalized.update(
            {
                "content_type": analysis.content_type,
                "company": analysis.company or "",
                "job_role": analysis.job_role or "",
                "job_direction": analysis.job_direction or "",
                "summary": analysis.summary or "",
                "interview_questions": analysis.interview_questions or [],
                "question_points": analysis.question_points or [],
                "is_interview_experience": analysis.is_interview_experience,
            }
        )
        analysis.normalized_json = normalized

        kb_document = session.query(KBDocument).filter(KBDocument.raw_post_id == raw_post.id).one_or_none()
        if analysis.is_interview_experience and content_type == "interview_note":
            doc_title, path = exporter.export(raw_post, analysis)
            if kb_document:
                kb_document.doc_title = doc_title
                kb_document.markdown_path = str(path)
            else:
                session.add(KBDocument(raw_post_id=raw_post.id, doc_title=doc_title, markdown_path=str(path)))
        elif kb_document:
            session.delete(kb_document)

        raw_post.status = "processed" if analysis.is_interview_experience else "processed"
        session.commit()


def _delete_single_post(raw_post_id: int) -> None:
    with session_factory() as session:
        session: Session
        raw_post = session.get(RawPost, raw_post_id)
        if raw_post is None:
            raise ValueError("记录不存在")

        kb_document = session.query(KBDocument).filter(KBDocument.raw_post_id == raw_post.id).one_or_none()
        if kb_document is not None:
            session.delete(kb_document)

        analysis = session.query(PostAnalysis).filter(PostAnalysis.raw_post_id == raw_post.id).one_or_none()
        if analysis is not None:
            session.delete(analysis)

        session.delete(raw_post)
        session.commit()


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


def _serialize_record(raw_post: RawPost, analysis: Optional[PostAnalysis], kb_document: Optional[KBDocument]) -> dict:
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
            "content_type": analysis.content_type if analysis else "",
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
            "llm_error": (analysis.normalized_json or {}).get("llm_error", "") if analysis else "",
            "llm_fallback": (analysis.normalized_json or {}).get("llm_fallback", False) if analysis else False,
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
        if value.tzinfo is not None:
            try:
                value = value.astimezone(ZoneInfo(settings.timezone))
            except Exception:
                pass
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _normalize_form_text(value: str) -> str:
    text = str(value or "")
    if "?" not in text:
        return text
    if not any(ord(ch) > 127 for ch in text):
        return text
    return text


def _parse_multiline_list(value: str) -> List[str]:
    items = []
    for line in str(value or "").splitlines():
        cleaned = line.strip().lstrip("-").strip()
        if cleaned:
            items.append(cleaned)
    return items
