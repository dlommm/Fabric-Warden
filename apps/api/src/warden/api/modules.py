"""Module Center API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from warden.api.deps import DB, Allowed, State
from warden.modules.catalog import CATALOG, PRESETS
from warden.modules.service import ModuleError, dependency_closure, dependents_of
from warden.security.rbac import P

router = APIRouter(prefix="/api/modules", tags=["modules"])


@router.get("")
async def list_modules(db: DB, state: State, principal: Allowed(P.MODULES_READ)) -> dict:
    states = await state.modules.states(db)
    enabled = await state.modules.enabled_ids(db)
    jobs_by_module: dict[str, list[str]] = {}
    for spec in state.scheduler.jobs.values():
        jobs_by_module.setdefault(spec.module_id, []).append(spec.id)
    out = []
    for m in CATALOG.values():
        s = states.get(m.id)
        out.append(
            {
                **m.model_dump(mode="json"),
                "enabled": m.id in enabled,
                "installed_version": s.installed_version if s else None,
                "enabled_at": s.enabled_at.isoformat() if s and s.enabled_at else None,
                "dependency_closure": dependency_closure(m.id),
                "dependents": dependents_of(m.id),
                "registered_jobs": jobs_by_module.get(m.id, []),
            }
        )
    return {
        "modules": out,
        "presets": [{"id": k, "name": v["name"], "modules": v["modules"]} for k, v in PRESETS.items()],
    }


class EnableIn(BaseModel):
    with_dependencies: bool = False


class DisableIn(BaseModel):
    cascade: bool = False


def _err(e: ModuleError) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"message": str(e), "missing": e.missing, "dependents": e.dependents},
    )


@router.post("/{module_id}/enable")
async def enable(module_id: str, body: EnableIn, db: DB, state: State, principal: Allowed(P.MODULES_MANAGE)) -> dict:
    try:
        return {"enabled": await state.modules.enable(db, module_id, principal.actor, with_dependencies=body.with_dependencies)}
    except ModuleError as e:
        raise _err(e) from e


@router.post("/{module_id}/disable")
async def disable(module_id: str, body: DisableIn, db: DB, state: State, principal: Allowed(P.MODULES_MANAGE)) -> dict:
    try:
        return {"disabled": await state.modules.disable(db, module_id, principal.actor, cascade=body.cascade)}
    except ModuleError as e:
        raise _err(e) from e


@router.post("/presets/{preset_id}/apply")
async def apply_preset(preset_id: str, db: DB, state: State, principal: Allowed(P.MODULES_MANAGE)) -> dict:
    try:
        r = await state.modules.apply_preset(db, preset_id, principal.actor)
    except ModuleError as e:
        raise _err(e) from e
    return {"enabled": r.enabled, "already_enabled": r.already_enabled, "unavailable": r.unavailable}
