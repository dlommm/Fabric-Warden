"""Module lifecycle: enable/disable with dependency checks, presets, state view."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.audit import Actor
from warden.db import utcnow
from warden.events import MODULE_DISABLED, MODULE_ENABLED, Event, EventBus
from warden.models import ModuleState
from warden.modules.catalog import CATALOG, PRESETS
from warden.modules.contracts import Maturity, ModuleManifest


class ModuleError(Exception):
    def __init__(self, message: str, *, missing: list[str] | None = None, dependents: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []
        self.dependents = dependents or []


@dataclass
class PresetResult:
    enabled: list[str] = field(default_factory=list)
    already_enabled: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


def dependency_closure(module_id: str) -> list[str]:
    """All transitive requirements of a module, dependencies first."""
    order: list[str] = []

    def visit(mid: str, stack: tuple[str, ...]) -> None:
        if mid in stack:
            raise ModuleError(f"dependency cycle: {' -> '.join((*stack, mid))}")
        for dep in CATALOG[mid].requires:
            visit(dep, (*stack, mid))
        if mid not in order:
            order.append(mid)

    visit(module_id, ())
    return order


def dependents_of(module_id: str) -> list[str]:
    return [m.id for m in CATALOG.values() if module_id in m.requires]


class ModuleService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    async def states(self, db: AsyncSession) -> dict[str, ModuleState]:
        rows = await db.scalars(select(ModuleState))
        return {r.module_id: r for r in rows}

    async def enabled_ids(self, db: AsyncSession) -> set[str]:
        states = await self.states(db)
        ids = {mid for mid, s in states.items() if s.enabled and mid in CATALOG}
        ids |= {m.id for m in CATALOG.values() if m.always_enabled}
        return ids

    async def is_enabled(self, db: AsyncSession, module_id: str) -> bool:
        return module_id in await self.enabled_ids(db)

    async def ensure_core(self, db: AsyncSession) -> None:
        states = await self.states(db)
        for m in CATALOG.values():
            if m.always_enabled and m.id not in states:
                db.add(ModuleState(module_id=m.id, enabled=True, installed_version=m.version, enabled_at=utcnow()))
        await db.flush()

    async def enable(self, db: AsyncSession, module_id: str, actor: Actor, *, with_dependencies: bool = False) -> list[str]:
        manifest = self._get(module_id)
        closure = dependency_closure(module_id)
        unbuildable = [m for m in closure if CATALOG[m].maturity == Maturity.PLANNED]
        if unbuildable:
            raise ModuleError(
                f"{manifest.name} cannot be enabled: {', '.join(unbuildable)} not yet implemented",
                missing=unbuildable,
            )
        enabled = await self.enabled_ids(db)
        missing = [m for m in closure if m != module_id and m not in enabled]
        if missing and not with_dependencies:
            raise ModuleError(f"{manifest.name} requires: {', '.join(missing)}", missing=missing)
        newly = [m for m in closure if m not in enabled]
        for mid in newly:
            await self._set(db, CATALOG[mid], True)
            await audit.record(db, actor, "module.enable", target_type="module", target_id=mid)
            await self.bus.publish(db, Event(type=MODULE_ENABLED, source="core", payload={"module_id": mid}))
        return newly

    async def disable(self, db: AsyncSession, module_id: str, actor: Actor, *, cascade: bool = False) -> list[str]:
        manifest = self._get(module_id)
        if manifest.always_enabled:
            raise ModuleError(f"{manifest.name} is required and cannot be disabled")
        enabled = await self.enabled_ids(db)
        if module_id not in enabled:
            return []
        to_disable = self._enabled_dependents(module_id, enabled)
        if to_disable and not cascade:
            raise ModuleError(f"{manifest.name} is required by: {', '.join(to_disable)}", dependents=to_disable)
        disabled: list[str] = []
        for mid in [*to_disable, module_id]:
            await self._set(db, CATALOG[mid], False)
            await audit.record(db, actor, "module.disable", target_type="module", target_id=mid)
            await self.bus.publish(db, Event(type=MODULE_DISABLED, source="core", payload={"module_id": mid}))
            disabled.append(mid)
        return disabled

    async def apply_preset(self, db: AsyncSession, preset_id: str, actor: Actor) -> PresetResult:
        preset = PRESETS.get(preset_id)
        if preset is None:
            raise ModuleError(f"unknown preset {preset_id}")
        result = PresetResult()
        for mid in preset["modules"]:  # type: ignore[attr-defined]
            enabled = await self.enabled_ids(db)
            if mid in enabled:
                result.already_enabled.append(mid)
                continue
            try:
                result.enabled += await self.enable(db, mid, actor, with_dependencies=True)
            except ModuleError:
                result.unavailable.append(mid)
        await audit.record(
            db,
            actor,
            "module.preset.apply",
            target_type="preset",
            target_id=preset_id,
            detail={"enabled": result.enabled, "unavailable": result.unavailable},
        )
        return result

    def _enabled_dependents(self, module_id: str, enabled: set[str]) -> list[str]:
        out: list[str] = []
        frontier = [module_id]
        while frontier:
            cur = frontier.pop()
            for dep in dependents_of(cur):
                if dep in enabled and dep not in out:
                    out.append(dep)
                    frontier.append(dep)
        # Disable leaves first.
        return list(reversed(out))

    def _get(self, module_id: str) -> ModuleManifest:
        if module_id not in CATALOG:
            raise ModuleError(f"unknown module {module_id}")
        return CATALOG[module_id]

    async def _set(self, db: AsyncSession, manifest: ModuleManifest, enabled: bool) -> None:
        state = await db.get(ModuleState, manifest.id)
        if state is None:
            state = ModuleState(module_id=manifest.id, installed_version=manifest.version, settings={})
            db.add(state)
        state.enabled = enabled
        if enabled:
            state.enabled_at = utcnow()
        else:
            state.disabled_at = utcnow()
        await db.flush()
