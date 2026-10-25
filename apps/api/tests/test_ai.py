"""Phase 7: AI providers and the Agent Center, against in-process fake model servers (never a real endpoint)."""

from __future__ import annotations

import pathlib
import re

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import PASSWORD
from warden.ai.providers import BUILTIN_PRICES, FALLBACK_BETA
from warden.ai.simulator import AiSimState, create_anthropic_app, create_ollama_app, create_openai_app
from warden.models import Alert, AlertRule, ChangeRequest, Credential, Device

ANTHROPIC_KEY = "sk-ant-test-0123456789"


class HostRouter(httpx.AsyncBaseTransport):
    """Routes requests to fake model servers by host; anything else fails as unreachable."""

    def __init__(self, apps: dict[str, object]) -> None:
        self.transports = {h: httpx.ASGITransport(app=a) for h, a in apps.items()}  # type: ignore[arg-type]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        t = self.transports.get(request.url.host)
        if t is None:
            raise httpx.ConnectError(f"no route to {request.url.host}", request=request)
        return await t.handle_async_request(request)


@pytest.fixture
async def ai(demo, owner, app):
    for m in ("ai-agents", "monitoring", "firewall", "change-engine", "logs"):
        r = await owner.post(f"/api/modules/{m}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    fakes = {"anthropic": AiSimState(api_key=ANTHROPIC_KEY, models=list(BUILTIN_PRICES)), "openai": AiSimState(), "ollama": AiSimState()}
    app.state.warden.ai_transport = HostRouter(
        {
            "api.anthropic.com": create_anthropic_app(fakes["anthropic"]),
            "lmstudio.local": create_openai_app(fakes["openai"]),
            "ollama.local": create_ollama_app(fakes["ollama"]),
        }
    )
    return fakes


async def _claude(owner, **extra) -> dict:
    r = await owner.post(
        "/api/ai/providers",
        json={"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com", "api_key": ANTHROPIC_KEY, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _alert(app) -> str:
    async with app.state.warden.db.sessionmaker() as db:
        dev = await db.scalar(select(Device).limit(1))
        rule = AlertRule(name="CPU high", metric="device.cpu_pct", comparator="gt", threshold=80, severity="warning")
        db.add(rule)
        await db.flush()
        a = Alert(
            rule_id=rule.id,
            entity_type="device",
            entity_id=dev.id,
            label=dev.name,
            state="firing",
            severity="warning",
            value=93.0,
            threshold=80,
        )
        db.add(a)
        await db.commit()
        return a.id


async def _audit(owner) -> list[dict]:
    return (await owner.get("/api/audit", params={"limit": 500})).json()["records"]


async def test_provider_secrets_stay_in_the_vault(ai, owner, app):
    bad = await owner.post(
        "/api/ai/providers/test", json={"kind": "anthropic", "base_url": "https://api.anthropic.com", "api_key": "wrong"}
    )
    assert bad.json()["ok"] is False and bad.json()["error_kind"] == "auth"
    p = await _claude(owner)
    assert p["key_fingerprint"] and "api_key" not in p and ANTHROPIC_KEY not in str(p)
    assert {m["id"] for m in p["models"]} == set(BUILTIN_PRICES) and p["default_model"] == "claude-opus-5-5"
    async with app.state.warden.db.sessionmaker() as db:
        cred = await db.scalar(select(Credential).where(Credential.kind == "ai-anthropic-key"))
        assert ANTHROPIC_KEY not in cred.ciphertext
    listed = (await owner.get("/api/ai/providers")).text
    assert ANTHROPIC_KEY not in listed
    t = (await owner.post(f"/api/ai/providers/{p['id']}/test")).json()
    assert t["ok"] and t["provider"]["status"] == "ok"
    actions = {r["action"] for r in await _audit(owner)}
    assert {"ai.provider.create", "ai.provider.test"} <= actions


async def test_local_providers_and_unreachable(ai, owner):
    for kind, url in (("openai_compatible", "http://lmstudio.local:1234/v1"), ("ollama", "http://ollama.local:11434")):
        r = (await owner.post("/api/ai/providers/test", json={"kind": kind, "base_url": url})).json()
        assert r["ok"] and r["models"][0]["id"] == "fabric-warden-demo", r
    down = (await owner.post("/api/ai/providers/test", json={"kind": "ollama", "base_url": "http://nowhere.local:11434"})).json()
    assert down["ok"] is False and down["error_kind"] == "unreachable" and down["hint"]


async def test_alert_explainer_on_claude(ai, owner, app):
    p = await _claude(owner)
    alert_id = await _alert(app)
    r = await owner.post("/api/ai/agents/alert-explainer/runs", json={"subject": {"alert_id": alert_id}})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "succeeded", run["error"]
    assert run["ai_generated"] is True and run["model"] == "claude-opus-5-5" and run["provider_id"] == p["id"]
    assert run["output"].startswith("Offline demo model")
    assert "get_alert" in {c["name"] for c in run["tool_calls"]}
    assert [e["kind"] for e in run["transcript"]][:3] == ["system", "user", "model"] and run["transcript"][-1]["kind"] == "finish"
    # Two model calls at the published Claude Opus 5.5 price.
    assert run["input_tokens"] == 840 and run["output_tokens"] == 180
    assert run["cost_usd"] == pytest.approx((840 * 4.0 + 180 * 20.0) / 1e6)
    sent = ai["anthropic"].requests
    assert sent[0]["body"]["tools"][0]["input_schema"]["type"] == "object"
    assert sent[0]["body"]["fallbacks"] == "default" and sent[0]["beta"] == FALLBACK_BETA
    # The assistant turn (thinking block included) is passed back unchanged; all tool results in one user message.
    second = sent[1]["body"]["messages"]
    assert second[1]["content"][0]["type"] == "thinking"
    assert second[2]["role"] == "user" and all(b["type"] == "tool_result" for b in second[2]["content"])
    usage = (await owner.get(f"/api/ai/providers/{p['id']}/usage")).json()
    assert usage["tokens"] == 1020 and len(usage["items"]) == 2
    actions = [r["action"] for r in await _audit(owner)]
    assert "ai.run.start" in actions and "ai.run.finish" in actions


async def test_refusal_is_reported(ai, owner, app):
    await _claude(owner)
    ai["anthropic"].refuse = True
    run = (await owner.post("/api/ai/agents/alert-explainer/runs", json={"subject": {"alert_id": await _alert(app)}})).json()
    assert run["status"] == "failed" and "declined" in run["error"]


async def test_ollama_agent_run(ai, owner, demo):
    r = await owner.post("/api/ai/providers", json={"name": "Ollama", "kind": "ollama", "base_url": "http://ollama.local:11434"})
    assert r.status_code == 201, r.text
    client = (await owner.get("/api/inventory/clients")).json()["items"][0]
    run = (await owner.post("/api/ai/agents/client-slowness/runs", json={"subject": {"client_id": client["id"]}})).json()
    assert run["status"] == "succeeded", run["error"]
    assert run["cost_usd"] == 0 and "get_client" in {c["name"] for c in run["tool_calls"]}
    assert ai["ollama"].requests[1]["body"]["messages"][-1]["role"] == "tool"


async def test_firewall_review_proposes_but_never_applies(ai, owner, app):
    await owner.post("/api/ai/providers/demo")
    sim = app.state.warden.simulator
    # At "explain" (the default) the propose tool is not even offered.
    run = (await owner.post("/api/ai/agents/firewall-review/runs", json={})).json()
    assert run["status"] == "succeeded" and run["proposals"] == [] and run["is_demo"] is True
    assert all(c["name"] != "propose_change" for c in run["tool_calls"])

    bad = await owner.put("/api/ai/agents/alert-explainer", json={"autonomy": "propose"})
    assert bad.status_code == 422
    assert (await owner.put("/api/ai/agents/firewall-review", json={"autonomy": "propose"})).status_code == 200
    run = (await owner.post("/api/ai/agents/firewall-review/runs", json={})).json()
    assert run["status"] == "succeeded", run["error"]
    assert len(run["proposals"]) == 1
    async with app.state.warden.db.sessionmaker() as db:
        cr = await db.get(ChangeRequest, run["proposals"][0]["id"])
        assert cr.status == "proposed" and cr.operation == "firewall_policy.set_logging"
        assert cr.reason.startswith("[AI agent firewall-review, RUN-") and cr.proposed_by == "owner"
    assert sim.writes == []
    rec = next(r for r in await _audit(owner) if r["action"] == "change.propose")
    assert rec["actor_type"] == "agent"


async def test_tools_check_the_invoking_users_permissions(ai, owner, app):
    await owner.post("/api/ai/providers/demo")
    tok = (await owner.post("/api/auth/tokens", json={"name": "ai-only", "permissions": ["ai:use"]})).json()["token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"authorization": f"Bearer {tok}"}
    ) as bot:
        run = (await bot.post("/api/ai/agents/firewall-review/runs", json={})).json()
    assert run["status"] == "succeeded"
    assert run["tool_calls"] and all(not c["ok"] and "inventory:read" in c["summary"] for c in run["tool_calls"])
    assert any(r["action"] == "ai.tool.denied" for r in await _audit(owner))


async def test_viewer_cannot_run_agents(ai, owner, app):
    await owner.post("/api/users", json={"username": "vic", "password": PASSWORD, "roles": ["viewer"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as v:
        await v.post("/api/auth/login", json={"username": "vic", "password": PASSWORD})
        assert (await v.post("/api/ai/agents/firewall-review/runs", json={})).status_code == 403
        assert (await v.get("/api/ai/providers")).status_code == 403


async def test_kill_switches(ai, owner, app):
    await owner.post("/api/ai/providers/demo")
    await owner.post("/api/users", json={"username": "oper", "password": PASSWORD, "roles": ["operator"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as op:
        await op.post("/api/auth/login", json={"username": "oper", "password": PASSWORD})
        assert (await op.post("/api/ai/kill-switch", json={"engaged": True, "reason": "drill"})).status_code == 200
        refused = await op.post("/api/ai/agents/firewall-review/runs", json={})
        assert refused.status_code == 409 and "kill switch" in refused.json()["detail"]
        # An operator can stop agents but not let them run again.
        assert (await op.post("/api/ai/kill-switch", json={"engaged": False})).status_code == 403
    assert (await owner.post("/api/ai/kill-switch", json={"engaged": False})).status_code == 200
    assert (await owner.post("/api/ai/agents/firewall-review/runs", json={})).status_code == 201
    await owner.put("/api/ai/agents/firewall-review", json={"enabled": False})
    off = await owner.post("/api/ai/agents/firewall-review/runs", json={})
    assert off.status_code == 409 and "switched off" in off.json()["detail"]
    actions = [r["action"] for r in await _audit(owner)]
    assert "ai.kill_switch" in actions and "ai.agent.configure" in actions and "ai.run.refused" in actions


async def test_budgets(ai, owner, app):
    p = (await owner.post("/api/ai/providers/demo")).json()
    sim = app.state.warden.ai_sim
    # Per-run cap: every turn asks for another tool call; 510 tokens a turn stops the run after the second turn.
    sim.script = [{"tool_calls": [{"name": "firewall_overview", "arguments": {}}]}] * 5
    await owner.put("/api/ai/agents/firewall-review", json={"max_tokens_per_run": 1000})
    run = (await owner.post("/api/ai/agents/firewall-review/runs", json={})).json()
    assert run["status"] == "budget_exceeded" and "Run token budget" in run["error"] and run["input_tokens"] + run["output_tokens"] == 1020
    # Monthly provider budget: already spent, so the next run stops before any model call.
    await owner.patch(f"/api/ai/providers/{p['id']}", json={"monthly_token_limit": 1000})
    sim.script = []
    before = len(sim.requests)
    run = (await owner.post("/api/ai/agents/firewall-review/runs", json={})).json()
    assert run["status"] == "budget_exceeded" and "this month" in run["error"] and len(sim.requests) == before
    assert (await owner.get(f"/api/ai/providers/{p['id']}/usage")).json()["budget_problem"]


async def test_prompt_injection_cannot_reach_a_write(ai, owner, app):
    await owner.post("/api/ai/providers/demo")
    sim = app.state.warden.ai_sim
    sim.script = [
        {"tool_calls": [{"name": "approve_change", "arguments": {"id": "x"}}, {"name": "firewall_overview", "arguments": {"site_id": 7}}]},
        {
            "tool_calls": [
                {"name": "propose_change", "arguments": {"operation": "device.restart", "target_id": "x", "reason": "log said so"}}
            ]
        },
        {"text": "done"},
    ]
    run = (await owner.post("/api/ai/agents/firewall-review/runs", json={})).json()
    results = [c["summary"] for c in run["tool_calls"]]
    assert "There is no tool named approve_change" in results[0]
    assert "must be a string" in results[1]
    # propose_change is not offered at "explain" autonomy, so calling it anyway is an unknown tool.
    assert "There is no tool named propose_change" in results[2]
    assert run["proposals"] == [] and app.state.warden.simulator.writes == []


def test_ai_package_has_no_path_to_approve_or_apply():
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "warden" / "ai"
    text = "\n".join(p.read_text() for p in root.glob("*.py"))
    assert "engine.approve" not in text and "engine.apply" not in text and "_write_gate" not in text
    assert "unifi_client" not in text and "WriteAuthorization" not in text
    # Phase 8 ("act after approval") keeps this: the only engine function the AI package calls is propose; it imports
    # nothing from the UniFi integration or the change routers (the approval that applies lives in api/changes.py).
    assert set(re.findall(r"\bengine\.(\w+)", text)) == {"propose"}
    assert "warden.integrations" not in text and "warden.api.changes" not in text and "warden.api import changes" not in text
    assert "approve(" not in text and ".apply(" not in text


def test_hosted_presets_and_versioned_urls():
    from warden.ai.providers import PRESETS, OpenAICompatibleAdapter, is_local_url

    ids = {p["id"] for p in PRESETS}
    assert {"openrouter", "groq", "gemini", "openai", "mistral"} <= ids
    assert all(p["kind"] == "openai_compatible" and p["needs_key"] and p["base_url"].startswith("https://") for p in PRESETS)
    v1 = lambda u: OpenAICompatibleAdapter(u)._v1()  # noqa: E731
    assert v1("https://generativelanguage.googleapis.com/v1beta/openai/") == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert v1("https://api.groq.com/openai/v1") == "https://api.groq.com/openai/v1"
    assert v1("https://api.fireworks.ai/inference/v1") == "https://api.fireworks.ai/inference/v1"
    assert v1("http://localhost:1234") == "http://localhost:1234/v1"
    assert v1("http://vllm.lan:8000/") == "http://vllm.lan:8000/v1"
    # Where prompts go: hosted services are not "your server".
    assert not any(is_local_url(p["base_url"]) for p in PRESETS)
    for url in (
        "http://localhost:11434",
        "http://10.0.30.5:1234/v1",
        "http://ollama:11434",
        "http://box.local:1234",
        "http://[fd00::5]:8000",
    ):
        assert is_local_url(url), url


async def test_kinds_lists_presets_and_hosted_url_is_not_local(ai, owner):
    kinds = (await owner.get("/api/ai/providers/kinds")).json()
    assert {p["id"] for p in kinds["presets"]} >= {"openrouter", "groq", "gemini"}
    listed = (await owner.get("/api/ai/providers")).json()["items"]
    assert all(p["local"] == (p["is_demo"] or ".local" in p["base_url"]) for p in listed)
