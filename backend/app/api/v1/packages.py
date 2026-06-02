"""GrovePackage import/export endpoints.

A GrovePackage is a single YAML file describing the full Grove-side domain
config (document classes, entity types, relationship definitions, dossier
classes, playbooks). Teams keep the file in their own repo and apply it
idempotently against a Grove deployment.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CallerIdentity, get_caller, require_permission
from app.db import get_session
from app.schemas.package import (
    PackageImportResult,
    PackageValidateResult,
)
from app.services import package as package_service
from app.services.sinas import Management, get_management

router = APIRouter(prefix="/packages", tags=["packages"])


def _require_sinas_token(caller: CallerIdentity) -> str:
    if caller.sinas_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "package management requires a Sinas token",
        )
    return caller.sinas_token


@router.post("/validate", response_model=PackageValidateResult)
async def validate_package(yaml_text: str = Body(..., media_type="application/x-yaml")):
    return package_service.validate(yaml_text)


@router.post(
    "/import",
    response_model=PackageImportResult,
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def import_package(
    yaml_text: str = Body(..., media_type="application/x-yaml"),
    prune: bool = Query(True, description="Delete managed resources missing from the manifest"),
    caller: CallerIdentity = Depends(get_caller),
    session: AsyncSession = Depends(get_session),
    mgmt: Management = Depends(get_management),
):
    token = _require_sinas_token(caller)
    try:
        return await package_service.apply(
            yaml_text, session=session, mgmt=mgmt, sinas_token=token, prune=prune
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get(
    "/{name}/export",
    dependencies=[Depends(require_permission("grove.admin:all"))],
)
async def export_package(
    name: str,
    version: str = Query("0.0.0"),
    caller: CallerIdentity = Depends(get_caller),
    session: AsyncSession = Depends(get_session),
    mgmt: Management = Depends(get_management),
):
    token = _require_sinas_token(caller)
    yaml_text = await package_service.export_package(
        name, session=session, mgmt=mgmt, sinas_token=token, version=version
    )
    return Response(content=yaml_text, media_type="application/x-yaml")
