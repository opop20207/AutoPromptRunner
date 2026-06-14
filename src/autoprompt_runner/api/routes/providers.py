"""Provider profile routes. Thin handlers over storage + autoprompt_runner.providers.

Availability is reported via command discovery only (shutil.which); no real agent is ever
executed here. Profiles store no secrets.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException

from ... import providers, storage
from ...models import ProviderProfile
from ..dependencies import get_db_path
from ..schemas import (
    ProviderAvailabilityResponse,
    ProviderProfileCreateRequest,
    ProviderProfileResponse,
    ProviderProfileUpdateRequest,
)

router = APIRouter(prefix="/providers", tags=["providers"])


def _to_response(profile: ProviderProfile) -> ProviderProfileResponse:
    return ProviderProfileResponse(
        id=profile.id,
        name=profile.name,
        type=profile.type,
        command=profile.command,
        default_timeout_seconds=profile.default_timeout_seconds,
        default_args=profile.default_args,
        enabled=profile.enabled,
        available=providers.check_provider_available(profile),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _require(db_path: str, name: str) -> ProviderProfile:
    profile = storage.get_provider_profile_by_name(db_path, name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"provider profile '{name}' not found")
    return profile


@router.post("/seed")
def seed_providers(db_path: str = Depends(get_db_path)) -> dict:
    return providers.seed_default_provider_profiles(db_path)


@router.get("", response_model=List[ProviderProfileResponse])
def list_providers(db_path: str = Depends(get_db_path)) -> List[ProviderProfileResponse]:
    return [_to_response(p) for p in storage.list_provider_profiles(db_path)]


@router.post("", response_model=ProviderProfileResponse)
def create_provider(body: ProviderProfileCreateRequest, db_path: str = Depends(get_db_path)) -> ProviderProfileResponse:
    if storage.get_provider_profile_by_name(db_path, body.name) is not None:
        raise HTTPException(status_code=400, detail=f"provider profile '{body.name}' already exists")
    try:
        provider_type = providers.validate_provider_type(body.type)
        command = providers.validate_provider_command(body.command)
        timeout = providers.validate_provider_timeout(body.default_timeout_seconds)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    profile_id = storage.create_provider_profile(
        db_path, name=body.name, type=provider_type, command=command,
        default_timeout_seconds=timeout, default_args=body.default_args, enabled=body.enabled,
    )
    return _to_response(storage.get_provider_profile_by_id(db_path, profile_id))


@router.get("/{provider_name}", response_model=ProviderProfileResponse)
def get_provider(provider_name: str, db_path: str = Depends(get_db_path)) -> ProviderProfileResponse:
    return _to_response(_require(db_path, provider_name))


@router.patch("/{provider_name}", response_model=ProviderProfileResponse)
def update_provider(
    provider_name: str, body: ProviderProfileUpdateRequest, db_path: str = Depends(get_db_path)
) -> ProviderProfileResponse:
    profile = _require(db_path, provider_name)
    fields = body.model_dump(exclude_unset=True)
    try:
        new_type = providers.validate_provider_type(fields["type"]) if "type" in fields else None
        new_command = providers.validate_provider_command(fields["command"]) if "command" in fields else None
        new_timeout = (
            providers.validate_provider_timeout(fields["default_timeout_seconds"])
            if "default_timeout_seconds" in fields
            else None
        )
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    storage.update_provider_profile(
        db_path, profile.id,
        type=new_type, command=new_command, default_timeout_seconds=new_timeout,
        default_args=fields["default_args"] if "default_args" in fields else storage._UNSET,
        enabled=fields.get("enabled"),
    )
    return _to_response(storage.get_provider_profile_by_id(db_path, profile.id))


@router.post("/{provider_name}/enable", response_model=ProviderProfileResponse)
def enable_provider(provider_name: str, db_path: str = Depends(get_db_path)) -> ProviderProfileResponse:
    profile = _require(db_path, provider_name)
    storage.set_provider_enabled(db_path, profile.id, True)
    return _to_response(storage.get_provider_profile_by_id(db_path, profile.id))


@router.post("/{provider_name}/disable", response_model=ProviderProfileResponse)
def disable_provider(provider_name: str, db_path: str = Depends(get_db_path)) -> ProviderProfileResponse:
    profile = _require(db_path, provider_name)
    storage.set_provider_enabled(db_path, profile.id, False)
    return _to_response(storage.get_provider_profile_by_id(db_path, profile.id))


@router.delete("/{provider_name}")
def delete_provider(provider_name: str, db_path: str = Depends(get_db_path)) -> dict:
    profile = _require(db_path, provider_name)
    storage.delete_provider_profile(db_path, profile.id)
    return {"deleted": provider_name}


@router.get("/{provider_name}/check", response_model=ProviderAvailabilityResponse)
def check_provider(provider_name: str, db_path: str = Depends(get_db_path)) -> ProviderAvailabilityResponse:
    profile = _require(db_path, provider_name)
    return ProviderAvailabilityResponse(
        name=profile.name, type=profile.type, command=profile.command,
        available=providers.check_provider_available(profile),
    )
