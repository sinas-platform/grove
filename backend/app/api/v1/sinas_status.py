"""Sinas integration status — is the sinas-grove package installed in Sinas,
and at what version? Used by the admin UI to surface drift / missing install.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import CallerIdentity, get_caller
from app.config import get_settings
from app.services.sinas import Management, get_management

router = APIRouter(prefix="/sinas-status", tags=["sinas-status"])

EXPECTED_PACKAGE_NAME = "sinas-grove"
EXPECTED_PACKAGE_VERSION = "0.1.6"


class SinasStatusOut(BaseModel):
    sinas_url: str
    package_name: str
    expected_version: str
    installed: bool
    installed_version: str | None
    drift: bool
    note: str | None = None


@router.get("", response_model=SinasStatusOut)
async def get_sinas_status(
    caller: CallerIdentity = Depends(get_caller),
    mgmt: Management = Depends(get_management),
) -> SinasStatusOut:
    settings = get_settings()
    if caller.sinas_token is None:
        return SinasStatusOut(
            sinas_url=settings.sinas_url,
            package_name=EXPECTED_PACKAGE_NAME,
            expected_version=EXPECTED_PACKAGE_VERSION,
            installed=False,
            installed_version=None,
            drift=False,
            note="cannot query Sinas — no token available",
        )

    pkg = await mgmt.get_installed_package(caller.sinas_token, EXPECTED_PACKAGE_NAME)
    if pkg is None:
        return SinasStatusOut(
            sinas_url=settings.sinas_url,
            package_name=EXPECTED_PACKAGE_NAME,
            expected_version=EXPECTED_PACKAGE_VERSION,
            installed=False,
            installed_version=None,
            drift=False,
            note="package not installed in Sinas — install via `sinas package install ./package/sinas-grove.yaml`",
        )

    installed_version = pkg.get("version") or pkg.get("package", {}).get("version")
    drift = installed_version != EXPECTED_PACKAGE_VERSION
    return SinasStatusOut(
        sinas_url=settings.sinas_url,
        package_name=EXPECTED_PACKAGE_NAME,
        expected_version=EXPECTED_PACKAGE_VERSION,
        installed=True,
        installed_version=installed_version,
        drift=drift,
        note=None if not drift else "installed version differs from this Grove build",
    )
