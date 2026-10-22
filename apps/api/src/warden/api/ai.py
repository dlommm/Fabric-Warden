"""AI Providers and the AI Agent Center.

Providers: connections to local (Ollama, OpenAI-compatible servers such as LM
Studio) and API (Anthropic Claude) model providers. API keys live in the vault;
responses carry only a fingerprint. Usage and monthly budgets are per provider.

Agents: the built-in agents (warden.ai.agents), their autonomy and limits,
the global and per-agent kill switches, runs with their evidence (in the
request or in the background, with a stop button), schedules, and agent notes
(memory). Nothing here writes to a controller; an agent at "propose" or "act
after approval" can only create a change proposal through the change engine.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from warden import audit
from warden.ai import agents as A
from warden.ai import tools as T
from warden.ai.providers import ADAPTERS, BUILTIN_PRICES, HINTS, KINDS, ProviderError
from warden.ai.simulator import DEMO_BASE_URL, DEMO_MODEL
from warden.api.deps import DB, Allowed, State, require_module
from warden.db import utcnow
from warden.models import (
    AiAgentConfig,
    AiAgentNote,
    AiAgentRun,
    AiAgentSchedule,
    AiProvider,
    AiUsage,
    ChangeRequest,
    Credential,
    Setting,
    SocCase,
)
from warden.security.rbac import P

providers_router = APIRouter(prefix="/api/ai/providers", tags=["ai-providers"], dependencies=[Depends(require_module("ai-providers"))])
agents_router = APIRouter(prefix="/api/ai", tags=["ai-agents"], dependencies=[Depends(require_module("ai-agents"))])

User = Allowed(P.AI_USE)
Manager = Allowed(P.AI_MANAGE)

Kind = Literal["anthropic", "openai_compatible", "ollama"]


# -- providers ------------------------------------------------------------------------


async def _provider_out(db: AsyncSession, p: AiProvider) -> dict[str, Any]:
    fp = None
    if p.credential_id and (cred := await db.get(Credential, p.credential_id)):
        fp = cred.fingerprint
    tokens, cost = await A.month_usage(db, p.id)
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "base_url": p.base_url,
        "key_fingerprint": fp,
        "default_model": p.default_model,
        "enabled": p.enabled,
        "verify_tls": p.verify_tls,
        "status": p.status,
        "status_detail": p.status_detail,
        "last_checked_at": p.last_checked_at,
        "models": p.models or [],
        "monthly_token_limit": p.monthly_token_limit,
        "monthly_cost_limit_usd": p.monthly_cost_limit_usd,
        "price_input_per_mtok": p.price_input_per_mtok,
        "price_output_per_mtok": p.price_output_per_mtok,
        "refusal_fallback": bool((p.options or {}).get("refusal_fallback", True)) if p.kind == "anthropic" else None,
        "local": p.kind in ("ollama", "openai_compatible", "demo"),
        "usage": {"since": A.month_start(), "tokens": tokens, "cost_usd": round(cost, 6)},
        "is_demo": p.is_demo,
    }


async def _get_provider(db: AsyncSession, pid: str) -> AiProvider:
    p = await db.get(AiProvider, pid)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found")
    return p


class ProviderIn(BaseModel):
    kind: Kind
    base_url: str = Field(min_length=8, max_length=512, pattern=r"^https?://")
    api_key: str = Field(default="", max_length=1024)
    verify_tls: bool = True


class ProviderCreate(ProviderIn):
    name: str = Field(min_length=1, max_length=128)
    default_model: str = Field(default="", max_length=128)
    monthly_token_limit: int | None = Field(default=None, ge=0)
    monthly_cost_limit_usd: float | None = Field(default=None, ge=0)
    price_input_per_mtok: float = Field(default=0.0, ge=0)
    price_output_per_mtok: float = Field(default=0.0, ge=0)
    refusal_fallback: bool = True


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=8, max_length=512, pattern=r"^https?://")
    api_key: str | None = Field(default=None, min_length=1, max_length=1024)
    enabled: bool | None = None
    verify_tls: bool | None = None
    default_model: str | None = Field(default=None, max_length=128)
    monthly_token_limit: int | None = Field(default=None, ge=0)
    monthly_cost_limit_usd: float | None = Field(default=None, ge=0)
    price_input_per_mtok: float | None = Field(default=None, ge=0)
    price_output_per_mtok: float | None = Field(default=None, ge=0)
    refusal_fallback: bool | None = None
    clear_token_limit: bool = False
    clear_cost_limit: bool = False


async def _probe(
    state: State, kind: str, base_url: str, key: str | None, verify_tls: bool, tmp: AiProvider | None = None
) -> dict[str, Any]:
    probe = tmp or AiProvider(name="test", kind=kind, base_url=base_url, verify_tls=verify_tls, is_demo=False)
    adapter = ADAPTERS[kind](base_url, key or None, transport=state.ai_transport_for(probe), verify_tls=verify_tls)
    try:
        models = await adapter.list_models()
    except ProviderError as e:
        return {"ok": False, "error_kind": e.kind, "error": str(e), "hint": HINTS.get(e.kind, HINTS["error"]), "models": []}
    return {"ok": True, "models": models}


@providers_router.get("/kinds")
async def kinds(principal: User) -> dict:
    return {
        "kinds": [{"id": k, **v} for k, v in KINDS.items()],
        "prices": [{"model": m, "input_per_mtok": i, "output_per_mtok": o} for m, (i, o) in BUILTIN_PRICES.items()],
    }


@providers_router.get("")
async def list_providers(db: DB, principal: User) -> dict:
    rows = await db.scalars(select(AiProvider).order_by(AiProvider.is_demo, AiProvider.name))
    return {"items": [await _provider_out(db, p) for p in rows]}


@providers_router.post("/test")
async def test_unsaved(body: ProviderIn, db: DB, state: State, principal: Manager) -> dict:
    result = await _probe(state, body.kind, body.base_url.rstrip("/"), body.api_key, body.verify_tls)
    await audit.record(
        db,
        principal.actor,
        "ai.provider.test",
        outcome="success" if result["ok"] else "failure",
        detail={"kind": body.kind, "base_url": body.base_url},
    )
    return result


@providers_router.post("", status_code=status.HTTP_201_CREATED)
async def create_provider(body: ProviderCreate, db: DB, state: State, principal: Manager) -> dict:
    if KINDS[body.kind]["needs_key"] and not body.api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This provider needs an API key")
    result = await _probe(state, body.kind, body.base_url.rstrip("/"), body.api_key, body.verify_tls)
    if not result["ok"]:
        await audit.record(
            db, principal.actor, "ai.provider.create", outcome="failure", detail={"kind": body.kind, "error": result["error"][:300]}
        )
        await db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, result)
    p = AiProvider(
        name=body.name,
        kind=body.kind,
        base_url=body.base_url.rstrip("/"),
        verify_tls=body.verify_tls,
        default_model=body.default_model or (result["models"][0]["id"] if result["models"] else ""),
        monthly_token_limit=body.monthly_token_limit,
        monthly_cost_limit_usd=body.monthly_cost_limit_usd,
        price_input_per_mtok=body.price_input_per_mtok,
        price_output_per_mtok=body.price_output_per_mtok,
        options={"refusal_fallback": body.refusal_fallback} if body.kind == "anthropic" else {},
        models=result["models"],
        status="ok",
        last_checked_at=utcnow(),
    )
    if body.api_key:
        cred = await state.vault.store(db, kind=f"ai-{body.kind}-key", name=f"AI provider key for {body.name}", secret=body.api_key)
        p.credential_id = cred.id
    db.add(p)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "ai.provider.create",
        target_type="ai_provider",
        target_id=p.id,
        detail={"kind": p.kind, "base_url": p.base_url, "name": p.name},
    )
    return await _provider_out(db, p)


@providers_router.post("/demo", status_code=status.HTTP_201_CREATED)
async def create_demo(db: DB, principal: Manager) -> dict:
    existing = await db.scalar(select(AiProvider).where(AiProvider.is_demo.is_(True)))
    if existing:
        return await _provider_out(db, existing)
    p = AiProvider(
        name="Offline demo model",
        kind="demo",
        base_url=DEMO_BASE_URL,
        default_model=DEMO_MODEL,
        models=[{"id": DEMO_MODEL, "name": "Deterministic offline demo (not a language model)"}],
        status="ok",
        last_checked_at=utcnow(),
        is_demo=True,
    )
    db.add(p)
    await db.flush()
    await audit.record(db, principal.actor, "ai.provider.create", target_type="ai_provider", target_id=p.id, detail={"kind": "demo"})
    return await _provider_out(db, p)


@providers_router.patch("/{pid}")
async def update_provider(pid: str, body: ProviderPatch, db: DB, state: State, principal: Manager) -> dict:
    p = await _get_provider(db, pid)
    changed = body.model_dump(exclude_unset=True, exclude={"api_key"})
    for f in ("name", "base_url", "enabled", "verify_tls", "default_model", "price_input_per_mtok", "price_output_per_mtok"):
        v = getattr(body, f)
        if v is not None:
            setattr(p, f, v.rstrip("/") if f == "base_url" else v)
    if body.monthly_token_limit is not None or body.clear_token_limit:
        p.monthly_token_limit = None if body.clear_token_limit else body.monthly_token_limit
    if body.monthly_cost_limit_usd is not None or body.clear_cost_limit:
        p.monthly_cost_limit_usd = None if body.clear_cost_limit else body.monthly_cost_limit_usd
    if body.refusal_fallback is not None and p.kind == "anthropic":
        p.options = {**(p.options or {}), "refusal_fallback": body.refusal_fallback}
    if body.api_key:
        if p.is_demo:
            raise HTTPException(status.HTTP_409_CONFLICT, "The demo provider has no key")
        if p.credential_id and (cred := await db.get(Credential, p.credential_id)):
            await state.vault.rotate(db, cred, body.api_key)
        else:
            cred = await state.vault.store(db, kind=f"ai-{p.kind}-key", name=f"AI provider key for {p.name}", secret=body.api_key)
            p.credential_id = cred.id
        changed["api_key"] = "rotated"
    await db.flush()
    await audit.record(
        db, principal.actor, "ai.provider.update", target_type="ai_provider", target_id=p.id, detail={"changed": sorted(changed)}
    )
    return await _provider_out(db, p)


@providers_router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(pid: str, db: DB, principal: Manager) -> None:
    p = await _get_provider(db, pid)
    cred = await db.get(Credential, p.credential_id) if p.credential_id else None
    await audit.record(db, principal.actor, "ai.provider.delete", target_type="ai_provider", target_id=p.id, detail={"name": p.name})
    await db.delete(p)
    await db.flush()
    if cred:
        await db.delete(cred)


@providers_router.post("/{pid}/test")
async def test_provider(pid: str, db: DB, state: State, principal: Manager) -> dict:
    """Connect, list the models and record the result on the provider."""
    p = await _get_provider(db, pid)
    key = await state.vault.reveal(db, p.credential_id) if p.credential_id else None
    result = await _probe(state, p.kind, p.base_url, key, p.verify_tls, tmp=p)
    p.last_checked_at = utcnow()
    p.status = "ok" if result["ok"] else "error"
    p.status_detail = "" if result["ok"] else f"{result['error']}"[:500]
    if result["ok"]:
        p.models = result["models"]
        if not p.default_model and p.models:
            p.default_model = p.models[0]["id"]
    await audit.record(
        db, principal.actor, "ai.provider.test", target_type="ai_provider", target_id=p.id, outcome="success" if result["ok"] else "failure"
    )
    return {**result, "provider": await _provider_out(db, p)}


@providers_router.get("/{pid}/usage")
async def provider_usage(pid: str, db: DB, principal: User, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    p = await _get_provider(db, pid)
    tokens, cost = await A.month_usage(db, p.id)
    rows = await db.scalars(select(AiUsage).where(AiUsage.provider_id == p.id).order_by(AiUsage.at.desc()).limit(limit))
    runs = {r.id: r.number for r in await db.scalars(select(AiAgentRun).where(AiAgentRun.provider_id == p.id))}
    return {
        "since": A.month_start(),
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "monthly_token_limit": p.monthly_token_limit,
        "monthly_cost_limit_usd": p.monthly_cost_limit_usd,
        "budget_problem": await A.budget_problem(db, p),
        "items": [
            {
                "at": u.at,
                "model": u.model,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cost_usd": u.cost_usd,
                "run": f"RUN-{runs[u.run_id]:04d}" if u.run_id in runs else None,
                "run_id": u.run_id,
            }
            for u in rows
        ],
    }


# -- agents ---------------------------------------------------------------------------


def _cfg_out(cfg: AiAgentConfig) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "autonomy": cfg.autonomy,
        "provider_id": cfg.provider_id,
        "model": cfg.model,
        "max_tokens_per_run": cfg.max_tokens_per_run,
        "max_tool_calls": cfg.max_tool_calls,
        "max_turns": cfg.max_turns,
        "updated_at": cfg.updated_at,
        "updated_by": cfg.updated_by,
    }


def _spec_out(spec: A.AgentSpec) -> dict[str, Any]:
    return {
        "key": spec.key,
        "name": spec.name,
        "summary": spec.summary,
        "subject": [{"key": f.key, "label": f.label, "kind": f.kind, "required": f.required} for f in spec.subject],
        "tools": [
            {
                "name": t,
                "description": T.TOOLS[t].description,
                "permission": T.TOOLS[t].permission.value,
                "proposes": T.TOOLS[t].proposes,
                "stores": T.TOOLS[t].stores,
            }
            for t in [*spec.tools, "suggest_note"]
        ],
        "max_autonomy": spec.max_autonomy,
        "allowed_operations": list(spec.allowed_operations),
        "default_question": spec.default_question,
        "requires_modules": list(spec.requires_modules),
        "question_required": spec.question_required,
        "phase": spec.phase,
    }


@agents_router.get("/agents")
async def list_agents(db: DB, state: State, principal: User) -> dict:
    items = []
    for spec in A.AGENTS.values():
        cfg = await A.config_for(db, spec.key)
        last = await db.scalar(select(AiAgentRun).where(AiAgentRun.agent_key == spec.key).order_by(AiAgentRun.number.desc()).limit(1))
        missing = [m for m in spec.requires_modules if not await state.modules.is_enabled(db, m)]
        items.append(
            {
                **_spec_out(spec),
                "config": _cfg_out(cfg),
                "missing_modules": missing,
                "last_run": {"id": last.id, "ref": f"RUN-{last.number:04d}", "status": last.status, "started_at": last.started_at}
                if last
                else None,
            }
        )
    return {
        "items": items,
        "kill_switch": await A.kill_switch(db),
        "autonomy_levels": [
            {"id": "off", "label": "Off", "built": True},
            {"id": "explain", "label": "Explain (read-only)", "built": True},
            {"id": "propose", "label": "Propose changes for approval", "built": True},
            {"id": "act_with_approval", "label": "Act after approval", "built": True},
            {"id": "autonomous", "label": "Autonomous within guardrails", "built": False},
        ],
    }


class AgentConfigIn(BaseModel):
    enabled: bool | None = None
    autonomy: Literal["off", "explain", "propose", "act_with_approval"] | None = None
    provider_id: str | None = Field(default=None, max_length=36)
    clear_provider: bool = False
    model: str | None = Field(default=None, max_length=128)
    max_tokens_per_run: int | None = Field(default=None, ge=1000, le=2_000_000)
    max_tool_calls: int | None = Field(default=None, ge=1, le=50)
    max_turns: int | None = Field(default=None, ge=1, le=20)


@agents_router.put("/agents/{key}")
async def configure_agent(key: str, body: AgentConfigIn, db: DB, principal: Manager) -> dict:
    spec = A.AGENTS.get(key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if body.autonomy and A.AUTONOMY.index(body.autonomy) > A.AUTONOMY.index(spec.max_autonomy):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{spec.name} can be at most “{spec.max_autonomy}”")
    if body.provider_id:
        await _get_provider(db, body.provider_id)
    cfg = await db.get(AiAgentConfig, key)
    if cfg is None:
        cfg = await A.config_for(db, key)
        db.add(cfg)
    before = _cfg_out(cfg)
    for f in ("enabled", "autonomy", "provider_id", "model", "max_tokens_per_run", "max_tool_calls", "max_turns"):
        v = getattr(body, f)
        if v is not None:
            setattr(cfg, f, v)
    if body.clear_provider:
        cfg.provider_id = None
    cfg.updated_by = principal.user.username
    cfg.updated_at = utcnow()
    await db.flush()
    after = _cfg_out(cfg)
    changed = {
        k: {"before": before[k], "after": after[k]} for k in after if k not in ("updated_at", "updated_by") and before[k] != after[k]
    }
    await audit.record(db, principal.actor, "ai.agent.configure", target_type="ai_agent", target_id=key, detail={"changed": changed})
    return {**_spec_out(spec), "config": after}


class KillSwitchIn(BaseModel):
    engaged: bool
    reason: str = Field(default="", max_length=500)


@agents_router.post("/kill-switch")
async def set_kill_switch(body: KillSwitchIn, db: DB, state: State, principal: User) -> dict:
    """Anyone who may run agents can stop them all; only an AI manager can let them run again."""
    if not body.engaged and not principal.can(P.AI_MANAGE):
        await audit.record(db, principal.actor, "ai.kill_switch", outcome="denied", detail={"engaged": False})
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission ai:manage to release the kill switch")
    value = {"engaged": body.engaged, "reason": body.reason.strip(), "by": principal.user.username, "at": utcnow().isoformat()}
    row = await db.get(Setting, A.KILL_SWITCH_KEY)
    if row is None:
        db.add(Setting(key=A.KILL_SWITCH_KEY, value=value))
    else:
        row.value = value
    state.ai_stop = body.engaged
    await audit.record(db, principal.actor, "ai.kill_switch", detail={"engaged": body.engaged, "reason": value["reason"]})
    return value


def _run_out(r: AiAgentRun, full: bool = False) -> dict[str, Any]:
    spec = A.AGENTS.get(r.agent_key)
    out = {
        "id": r.id,
        "number": r.number,
        "ref": f"RUN-{r.number:04d}",
        "agent_key": r.agent_key,
        "agent_name": spec.name if spec else r.agent_key,
        "provider_id": r.provider_id,
        "provider_name": r.provider_name,
        "model": r.model,
        "autonomy": r.autonomy,
        "status": r.status,
        "requested_by": r.requested_by,
        "subject": r.subject,
        "question": r.question,
        "output": r.output,
        "ai_generated": True,
        "proposals": r.proposals,
        "trigger": r.trigger,
        "schedule_id": r.schedule_id,
        "cancel_requested": r.cancel_requested,
        "tool_call_count": len(r.tool_calls),
        "input_tokens": r.input_tokens,
        "output_tokens": r.output_tokens,
        "cost_usd": r.cost_usd,
        "error": r.error,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "is_demo": r.is_demo,
    }
    if full:
        out |= {"system_prompt": r.system_prompt, "transcript": r.transcript, "tool_calls": r.tool_calls}
    return out


class RunIn(BaseModel):
    subject: dict[str, str] = Field(default_factory=dict)
    question: str = Field(default="", max_length=2000)
    # Return at once with the run "running"; the page polls GET /runs/{id} for the transcript as it grows.
    background: bool = False


@agents_router.post("/agents/{key}/runs", status_code=status.HTTP_201_CREATED)
async def start_run(key: str, body: RunIn, db: DB, state: State, principal: User) -> dict:
    trigger = "ask" if key == "ask-network" else "manual"
    try:
        prepared = await A.prepare(db, state, principal, key, body.subject, body.question, trigger)
    except A.AgentRefused as e:
        await audit.record(
            db, principal.actor, "ai.run.refused", target_type="ai_agent", target_id=key, outcome="denied", detail={"reason": str(e)}
        )
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    if body.background:
        await db.commit()
        A.start_in_background(state, principal, prepared)
        return _run_out(prepared.run, full=True)
    return _run_out(await A.execute(db, state, principal, prepared), full=True)


@agents_router.post("/runs/{rid}/cancel")
async def cancel_run(rid: str, db: DB, state: State, principal: User) -> dict:
    """Stop a running run before its next model call. The person who started it or an AI manager may."""
    r = await db.get(AiAgentRun, rid)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if r.requested_by != principal.user.username and not principal.can(P.AI_MANAGE):
        await audit.record(db, principal.actor, "ai.run.cancel", target_type="ai_run", target_id=r.id, outcome="denied")
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who started the run or an AI manager can stop it")
    if r.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, f"RUN-{r.number:04d} is no longer running")
    state.ai_cancel.add(r.id)
    r.cancel_requested = True
    await audit.record(db, principal.actor, "ai.run.cancel", target_type="ai_run", target_id=r.id, detail={"number": r.number})
    return _run_out(r)


@agents_router.get("/runs")
async def list_runs(db: DB, principal: User, agent: str | None = None, limit: int = Query(default=100, ge=1, le=500)) -> dict:
    q = select(AiAgentRun).order_by(AiAgentRun.number.desc()).limit(limit)
    if agent:
        q = q.where(AiAgentRun.agent_key == agent)
    total = await db.scalar(select(func.count()).select_from(AiAgentRun))
    return {"items": [_run_out(r) for r in await db.scalars(q)], "total": total}


@agents_router.get("/runs/{rid}")
async def get_run(rid: str, db: DB, principal: User) -> dict:
    r = await db.get(AiAgentRun, rid)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return _run_out(r, full=True)


@agents_router.get("/subjects/sites")
async def subject_sites(db: DB, principal: User) -> dict:
    return {"items": await T.site_choices(db)}


@agents_router.get("/subjects/cases")
async def subject_cases(db: DB, principal: User) -> dict:
    rows = await db.scalars(select(SocCase).order_by(SocCase.updated_at.desc()).limit(100))
    return {"items": [{"id": c.id, "name": f"{c.title} ({c.status})", "is_demo": c.is_demo} for c in rows]}


@agents_router.get("/subjects/changes")
async def subject_changes(db: DB, principal: User) -> dict:
    rows = await db.scalars(select(ChangeRequest).order_by(ChangeRequest.number.desc()).limit(100))
    return {"items": [{"id": c.id, "name": f"CHG-{c.number:04d} · {c.title} ({c.status})", "is_demo": c.is_demo} for c in rows]}


# -- schedules ---------------------------------------------------------------------------


def _schedule_out(s: AiAgentSchedule) -> dict[str, Any]:
    spec = A.AGENTS.get(s.agent_key)
    return {
        "id": s.id,
        "agent_key": s.agent_key,
        "agent_name": spec.name if spec else s.agent_key,
        "name": s.name,
        "subject": s.subject,
        "question": s.question,
        "interval_minutes": s.interval_minutes,
        "max_runs_per_day": s.max_runs_per_day,
        "enabled": s.enabled,
        "run_as": s.run_as,
        "last_run_at": s.last_run_at,
        "last_run_id": s.last_run_id,
        "last_outcome": s.last_outcome,
        "last_detail": s.last_detail,
        "next_run_at": (s.last_run_at + timedelta(minutes=s.interval_minutes)) if s.last_run_at and s.enabled else None,
        "created_at": s.created_at,
    }


async def _get_schedule(db: AsyncSession, sid: str) -> AiAgentSchedule:
    s = await db.get(AiAgentSchedule, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    return s


class ScheduleIn(BaseModel):
    agent_key: str = Field(max_length=48)
    name: str = Field(min_length=1, max_length=128)
    subject: dict[str, str] = Field(default_factory=dict)
    question: str = Field(default="", max_length=2000)
    interval_minutes: int = Field(default=1440, ge=60, le=43_200)
    max_runs_per_day: int = Field(default=4, ge=1, le=24)
    enabled: bool = True


class SchedulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    question: str | None = Field(default=None, max_length=2000)
    interval_minutes: int | None = Field(default=None, ge=60, le=43_200)
    max_runs_per_day: int | None = Field(default=None, ge=1, le=24)
    enabled: bool | None = None


@agents_router.get("/schedules")
async def list_schedules(db: DB, principal: User) -> dict:
    rows = await db.scalars(select(AiAgentSchedule).order_by(AiAgentSchedule.created_at))
    return {"items": [_schedule_out(s) for s in rows]}


@agents_router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, db: DB, principal: Manager) -> dict:
    """A schedule always runs as the signed-in person who creates it, with that person's roles at run time."""
    spec = A.AGENTS.get(body.agent_key)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    if principal.via != "session":
        # A narrowed API token must not be able to start runs later with its owner's full rights.
        await audit.record(db, principal.actor, "ai.schedule.create", outcome="denied", detail={"reason": "not a session"})
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Create schedules from a signed-in session, not an API token")
    missing = [f.label for f in spec.subject if f.required and not body.subject.get(f.key)]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Choose a subject: {', '.join(missing)}")
    if spec.question_required and not body.question.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "This agent needs a question")
    s = AiAgentSchedule(
        agent_key=spec.key,
        name=body.name.strip(),
        subject={f.key: body.subject[f.key] for f in spec.subject if body.subject.get(f.key)},
        question=body.question.strip(),
        interval_minutes=body.interval_minutes,
        max_runs_per_day=body.max_runs_per_day,
        enabled=body.enabled,
        run_as_user_id=principal.user.id,
        run_as=principal.user.username,
    )
    db.add(s)
    await db.flush()
    await audit.record(
        db,
        principal.actor,
        "ai.schedule.create",
        target_type="ai_schedule",
        target_id=s.id,
        detail={"agent": s.agent_key, "name": s.name, "interval_minutes": s.interval_minutes, "max_runs_per_day": s.max_runs_per_day},
    )
    return _schedule_out(s)


@agents_router.patch("/schedules/{sid}")
async def update_schedule(sid: str, body: SchedulePatch, db: DB, principal: Manager) -> dict:
    s = await _get_schedule(db, sid)
    changed = body.model_dump(exclude_unset=True)
    for k, v in changed.items():
        if v is not None:
            setattr(s, k, v.strip() if isinstance(v, str) else v)
    await db.flush()
    await audit.record(
        db, principal.actor, "ai.schedule.update", target_type="ai_schedule", target_id=s.id, detail={"changed": sorted(changed)}
    )
    return _schedule_out(s)


@agents_router.delete("/schedules/{sid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(sid: str, db: DB, principal: Manager) -> None:
    s = await _get_schedule(db, sid)
    await audit.record(db, principal.actor, "ai.schedule.delete", target_type="ai_schedule", target_id=s.id, detail={"name": s.name})
    await db.delete(s)


@agents_router.post("/schedules/{sid}/run")
async def run_schedule_now(sid: str, db: DB, state: State, principal: Manager) -> dict:
    """Run a schedule once now, exactly as the scheduler would (as its creator, with the daily cap)."""
    s = await _get_schedule(db, sid)
    run = await A.run_schedule(db, state, s)
    return {"schedule": _schedule_out(s), "run": _run_out(run) if run else None}


# -- notes (agent memory) ----------------------------------------------------------------


def _note_out(n: AiAgentNote) -> dict[str, Any]:
    return {
        "id": n.id,
        "agent_key": n.agent_key,
        "text": n.text,
        "status": n.status,
        "source": n.source,
        "source_run_id": n.source_run_id,
        "created_by": n.created_by,
        "created_at": n.created_at,
        "decided_by": n.decided_by,
        "decided_at": n.decided_at,
        "ai_generated": n.source == "agent",
    }


async def _get_note(db: AsyncSession, nid: str) -> AiAgentNote:
    n = await db.get(AiAgentNote, nid)
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return n


class NoteIn(BaseModel):
    agent_key: str = Field(max_length=48)
    text: str = Field(min_length=1, max_length=T.MAX_NOTE_CHARS)


@agents_router.get("/notes")
async def list_notes(db: DB, principal: User, agent: str | None = None, include_rejected: bool = False) -> dict:
    q = select(AiAgentNote).order_by(AiAgentNote.created_at.desc())
    if agent:
        q = q.where(AiAgentNote.agent_key.in_((agent, "*")))
    if not include_rejected:
        q = q.where(AiAgentNote.status != "rejected")
    return {"items": [_note_out(n) for n in await db.scalars(q)]}


@agents_router.post("/notes", status_code=status.HTTP_201_CREATED)
async def create_note(body: NoteIn, db: DB, principal: Manager) -> dict:
    if body.agent_key != "*" and body.agent_key not in A.AGENTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    n = AiAgentNote(
        agent_key=body.agent_key, text=" ".join(body.text.split()), status="active", source="person", created_by=principal.user.username
    )
    db.add(n)
    await db.flush()
    await audit.record(
        db, principal.actor, "ai.note.create", target_type="ai_note", target_id=n.id, detail={"agent": n.agent_key, "text": n.text[:200]}
    )
    return _note_out(n)


class NoteDecision(BaseModel):
    accept: bool


@agents_router.post("/notes/{nid}/decide")
async def decide_note(nid: str, body: NoteDecision, db: DB, principal: Manager) -> dict:
    """Accept an agent's suggested note into its memory, or reject it. Only a pending note can be decided."""
    n = await _get_note(db, nid)
    if n.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This note is already {n.status}")
    n.status = "active" if body.accept else "rejected"
    n.decided_by, n.decided_at = principal.user.username, utcnow()
    await audit.record(
        db,
        principal.actor,
        "ai.note.accept" if body.accept else "ai.note.reject",
        target_type="ai_note",
        target_id=n.id,
        detail={"agent": n.agent_key},
    )
    return _note_out(n)


@agents_router.delete("/notes/{nid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(nid: str, db: DB, principal: Manager) -> None:
    n = await _get_note(db, nid)
    await audit.record(
        db, principal.actor, "ai.note.delete", target_type="ai_note", target_id=n.id, detail={"agent": n.agent_key, "text": n.text[:200]}
    )
    await db.delete(n)
