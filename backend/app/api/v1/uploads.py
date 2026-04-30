"""File upload — proxies to Sinas's grove/documents collection via the SDK.
The post-upload function then registers the file with Grove asynchronously."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sinas import SinasClient

from app.auth import CallerIdentity, get_caller
from app.config import get_settings

router = APIRouter(prefix="/uploads", tags=["uploads"])

GROVE_NAMESPACE = "grove"
GROVE_COLLECTION = "documents"


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile = File(...),
    metadata_json: str | None = Form(default=None),
    caller: CallerIdentity = Depends(get_caller),
):
    if caller.sinas_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "uploads require a Sinas token",
        )

    content = await file.read()
    metadata: dict | None = None
    if metadata_json:
        import json as _json

        try:
            metadata = _json.loads(metadata_json)
        except _json.JSONDecodeError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"metadata_json is not valid JSON: {exc}"
            ) from exc
    if metadata is None:
        metadata = {}
    metadata.setdefault("source", "manual")

    client = SinasClient(base_url=get_settings().sinas_url, token=caller.sinas_token)
    result = await asyncio.to_thread(
        client.files.upload_bytes,
        namespace=GROVE_NAMESPACE,
        collection=GROVE_COLLECTION,
        name=file.filename or "upload.bin",
        content=content,
        content_type=file.content_type or "application/octet-stream",
        file_metadata=metadata,
    )
    return {
        "status": "accepted",
        "filename": file.filename,
        "sinas_response": result,
        "note": "ingestion runs asynchronously — poll /api/v1/documents to see the registered document",
    }
