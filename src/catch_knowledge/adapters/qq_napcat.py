from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect


def create_qq_adapter_app(
    ingest_base_url: str,
    napcat_api_base_url: Optional[str] = None,
    napcat_access_token: Optional[str] = None,
    webhook_secret: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(title="Catch Knowledge QQ Adapter")
    ingest_base_url = ingest_base_url.rstrip("/")
    napcat_api_base_url = napcat_api_base_url.rstrip("/") if napcat_api_base_url else None
    ws_connections: Dict[str, WebSocket] = {}

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "catch-knowledge-qq-adapter",
            "ingest_base_url": ingest_base_url,
            "napcat_api_base_url": napcat_api_base_url,
        }

    @app.websocket("/qq/ws")
    async def qq_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        connection_id = str(uuid4())
        ws_connections[connection_id] = websocket
        print(f"[qq-adapter] WS connected: {connection_id}")
        try:
            while True:
                payload = await websocket.receive_json()
                print(
                    "[qq-adapter] WS payload:",
                    json.dumps(
                        {
                            "post_type": payload.get("post_type"),
                            "message_type": payload.get("message_type"),
                            "sub_type": payload.get("sub_type"),
                            "user_id": payload.get("user_id"),
                            "self_id": payload.get("self_id"),
                            "raw_message": payload.get("raw_message"),
                        },
                        ensure_ascii=False,
                    ),
                )
                if payload.get("post_type") != "message":
                    continue
                if payload.get("message_type") != "private":
                    continue

                sender_id = str(payload.get("user_id") or "")
                self_id = str(payload.get("self_id") or "")
                if self_id and sender_id == self_id:
                    continue

                text, image_refs = _extract_message_content(payload)
                if not text and not image_refs:
                    print("[qq-adapter] Ignored empty private message payload")
                    continue

                title = _infer_title(text=text, image_urls=image_refs)
                sender_name = (
                    payload.get("sender", {}).get("nickname")
                    or payload.get("sender", {}).get("card")
                    or sender_id
                )

                try:
                    print(
                        f"[qq-adapter] Forwarding private message from {sender_name} "
                        f"with text_length={len(text)} images={len(image_refs)} refs={image_refs}"
                    )
                    result = await _forward_to_ingest(
                        ingest_base_url=ingest_base_url,
                        napcat_api_base_url=napcat_api_base_url,
                        napcat_access_token=napcat_access_token,
                        title=title,
                        text=text,
                        source="qq",
                        sender=sender_name,
                        source_url=None,
                        image_refs=image_refs,
                    )
                    print(
                        "[qq-adapter] Ingest result:",
                        json.dumps(result, ensure_ascii=False),
                    )
                    await _send_ws_private_message(websocket, user_id=sender_id, result=result)
                except Exception as exc:
                    print(f"[qq-adapter] Forwarding failed: {repr(exc)}")
                    await _send_ws_error(websocket, user_id=sender_id, error=repr(exc))
        except WebSocketDisconnect:
            print(f"[qq-adapter] WS disconnected: {connection_id}")
            ws_connections.pop(connection_id, None)
        except Exception:
            ws_connections.pop(connection_id, None)
            raise

    @app.post("/qq/webhook")
    async def qq_webhook(
        request: Request,
        x_self_id: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        if webhook_secret:
            expected = f"Bearer {webhook_secret}"
            if authorization != expected:
                raise HTTPException(status_code=401, detail="invalid webhook token")

        payload = await request.json()
        if payload.get("post_type") != "message":
            return {"ok": True, "ignored": "post_type"}
        if payload.get("message_type") != "private":
            return {"ok": True, "ignored": "message_type"}

        sender_id = str(payload.get("user_id") or "")
        if x_self_id and sender_id == str(x_self_id):
            return {"ok": True, "ignored": "self_message"}

        text, image_refs = _extract_message_content(payload)
        if not text and not image_refs:
            return {"ok": True, "ignored": "empty_message"}

        title = _infer_title(text=text, image_urls=image_refs)
        sender_name = (
            payload.get("sender", {}).get("nickname")
            or payload.get("sender", {}).get("card")
            or sender_id
        )

        try:
            result = await _forward_to_ingest(
                ingest_base_url=ingest_base_url,
                napcat_api_base_url=napcat_api_base_url,
                napcat_access_token=napcat_access_token,
                title=title,
                text=text,
                source="qq",
                sender=sender_name,
                source_url=None,
                image_refs=image_refs,
            )
            await _reply_summary(
                napcat_api_base_url=napcat_api_base_url,
                napcat_access_token=napcat_access_token,
                user_id=sender_id,
                result=result,
            )
            return {"ok": True, "forwarded": True, "result": result}
        except Exception as exc:
            await _reply_error(
                napcat_api_base_url=napcat_api_base_url,
                napcat_access_token=napcat_access_token,
                user_id=sender_id,
                error=repr(exc),
            )
            return {"ok": False, "error": repr(exc)}

    return app


def _extract_message_content(payload: Dict[str, Any]) -> tuple[str, List[str]]:
    message = payload.get("message")
    if isinstance(message, str):
        return message.strip(), []

    text_parts: List[str] = []
    image_refs: List[str] = []
    if not isinstance(message, list):
        return "", []

    for segment in message:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            value = str(data.get("text") or "").strip()
            if value:
                text_parts.append(value)
        elif seg_type == "image":
            ref = str(data.get("url") or data.get("file") or "").strip()
            if ref:
                image_refs.append(ref)

    return "\n".join(text_parts).strip(), image_refs


def _infer_title(text: str, image_urls: List[str]) -> str:
    if text:
        compact = " ".join(text.split())
        return compact[:60]
    return f"QQ上传图片 {len(image_urls)} 张"


async def _forward_to_ingest(
    ingest_base_url: str,
    napcat_api_base_url: Optional[str],
    napcat_access_token: Optional[str],
    title: str,
    text: str,
    source: str,
    sender: str,
    source_url: Optional[str],
    image_refs: List[str],
) -> Dict[str, Any]:
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
        if not image_refs:
            response = await client.post(
                f"{ingest_base_url}/api/ingest/text",
                json={
                    "title": title,
                    "text": text,
                    "source": source,
                    "sender": sender,
                    "source_url": source_url,
                },
            )
            response.raise_for_status()
            return response.json()

        temp_paths = await _download_images(
            client,
            image_refs=image_refs,
            napcat_api_base_url=napcat_api_base_url,
            napcat_access_token=napcat_access_token,
        )
        files = []
        opened_files = []
        try:
            for path in temp_paths:
                handle = open(path, "rb")
                opened_files.append(handle)
                files.append(("files", (path.name, handle, "application/octet-stream")))
            response = await client.post(
                f"{ingest_base_url}/api/ingest/message",
                data={
                    "title": title,
                    "text": text,
                    "source": source,
                    "sender": sender,
                    "source_url": source_url or "",
                },
                files=files,
            )
            response.raise_for_status()
            return response.json()
        finally:
            for handle in opened_files:
                handle.close()
            for path in temp_paths:
                path.unlink(missing_ok=True)


async def _download_images(
    client: httpx.AsyncClient,
    image_refs: List[str],
    napcat_api_base_url: Optional[str],
    napcat_access_token: Optional[str],
) -> List[Path]:
    temp_paths: List[Path] = []
    for index, image_ref in enumerate(image_refs, start=1):
        try:
            response = await _fetch_image_response(
                client=client,
                image_ref=image_ref,
                napcat_api_base_url=napcat_api_base_url,
                napcat_access_token=napcat_access_token,
            )
        except Exception as exc:
            raise RuntimeError(f"下载图片失败：{image_ref} -> {repr(exc)}") from exc
        response.raise_for_status()
        suffix = _infer_suffix(image_ref, response.headers.get("content-type"))
        temp_path = Path(tempfile.gettempdir()) / f"catch_knowledge_qq_{index}{suffix}"
        temp_path.write_bytes(response.content)
        temp_paths.append(temp_path)
    return temp_paths


async def _fetch_image_response(
    client: httpx.AsyncClient,
    image_ref: str,
    napcat_api_base_url: Optional[str],
    napcat_access_token: Optional[str],
) -> httpx.Response:
    if image_ref.startswith("http://") or image_ref.startswith("https://"):
        return await client.get(image_ref, follow_redirects=True)

    if not napcat_api_base_url:
        raise RuntimeError(f"Image ref is not an HTTP URL and NapCat API is unavailable: {image_ref}")

    headers: Dict[str, str] = {}
    if napcat_access_token:
        headers["Authorization"] = f"Bearer {napcat_access_token}"

    api_response = await client.post(
        f"{napcat_api_base_url}/get_image",
        json={"file": image_ref},
        headers=headers,
    )
    api_response.raise_for_status()
    payload = api_response.json()
    image_data = payload.get("data") or {}
    resolved_url = str(image_data.get("url") or image_data.get("file") or image_data.get("path") or "").strip()
    if not resolved_url:
        raise RuntimeError(f"NapCat get_image did not return a usable URL for: {image_ref} -> {payload}")
    if Path(resolved_url).exists():
        return httpx.Response(200, content=Path(resolved_url).read_bytes(), request=httpx.Request("GET", resolved_url))
    return await client.get(resolved_url, follow_redirects=True)


def _infer_suffix(url: str, content_type: Optional[str]) -> str:
    lower_url = url.lower()
    if ".png" in lower_url or (content_type and "png" in content_type.lower()):
        return ".png"
    if ".webp" in lower_url or (content_type and "webp" in content_type.lower()):
        return ".webp"
    if ".gif" in lower_url or (content_type and "gif" in content_type.lower()):
        return ".gif"
    return ".jpg"


async def _reply_summary(
    napcat_api_base_url: Optional[str],
    napcat_access_token: Optional[str],
    user_id: str,
    result: Dict[str, Any],
) -> None:
    if not napcat_api_base_url:
        return

    record = result.get("record") or {}
    questions = record.get("interview_questions") or []
    points = record.get("question_points") or []
    lines = [
        "已收录",
        f"类型：{record.get('content_type') or 'unknown'}",
        f"状态：{record.get('status') or 'unknown'}",
    ]
    if questions:
        lines.append(f"题目：{questions[0]}")
    if points:
        lines.append(f"知识点：{'、'.join(points[:3])}")
    if record.get("raw_post_id"):
        lines.append(f"记录ID：{record['raw_post_id']}")

    await _send_private_message(
        napcat_api_base_url=napcat_api_base_url,
        napcat_access_token=napcat_access_token,
        user_id=user_id,
        message="\n".join(lines),
    )


async def _send_ws_private_message(
    websocket: WebSocket,
    user_id: str,
    result: Dict[str, Any],
) -> None:
    record = result.get("record") or {}
    questions = record.get("interview_questions") or []
    points = record.get("question_points") or []
    lines = [
        "已收录",
        f"类型：{record.get('content_type') or 'unknown'}",
        f"状态：{record.get('status') or 'unknown'}",
    ]
    if questions:
        lines.append(f"题目：{questions[0]}")
    if points:
        lines.append(f"知识点：{'、'.join(points[:3])}")
    if record.get("raw_post_id"):
        lines.append(f"记录ID：{record['raw_post_id']}")
    await websocket.send_json(
        {
            "action": "send_private_msg",
            "params": {"user_id": int(user_id), "message": "\n".join(lines)},
            "echo": f"reply-{record.get('raw_post_id') or uuid4()}",
        }
    )


async def _send_ws_error(
    websocket: WebSocket,
    user_id: str,
    error: str,
) -> None:
    await websocket.send_json(
        {
            "action": "send_private_msg",
            "params": {"user_id": int(user_id), "message": f"整理失败\n原因：{error}"},
            "echo": f"error-{uuid4()}",
        }
    )


async def _reply_error(
    napcat_api_base_url: Optional[str],
    napcat_access_token: Optional[str],
    user_id: str,
    error: str,
) -> None:
    if not napcat_api_base_url:
        return
    await _send_private_message(
        napcat_api_base_url=napcat_api_base_url,
        napcat_access_token=napcat_access_token,
        user_id=user_id,
        message=f"整理失败\n原因：{error}",
    )


async def _send_private_message(
    napcat_api_base_url: str,
    napcat_access_token: Optional[str],
    user_id: str,
    message: str,
) -> None:
    headers: Dict[str, str] = {}
    if napcat_access_token:
        headers["Authorization"] = f"Bearer {napcat_access_token}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), trust_env=False) as client:
        response = await client.post(
            f"{napcat_api_base_url}/send_private_msg",
            json={"user_id": int(user_id), "message": message},
            headers=headers,
        )
        response.raise_for_status()
