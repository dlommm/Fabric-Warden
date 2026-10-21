"""Phase 3: metric history, dashboards, alerting, notification channels and WAN."""

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from tests.conftest import PASSWORD
from warden import metrics, monitoring
from warden.db import utcnow
from warden.models import Alert, AlertRule, Credential, EventRecord, MetricSample, MetricSeries
from warden.security.rbac import ROLES, P


async def _sync(owner, ctrl):
    assert (await owner.post(f"/api/controllers/{ctrl['id']}/sync")).json()["status"] == "succeeded"


@pytest.fixture
async def phase3(demo, owner):
    for mid in ("analytics-studio", "monitoring", "notifications", "wan"):
        r = await owner.post(f"/api/modules/{mid}/enable", json={"with_dependencies": True})
        assert r.status_code == 200, r.text
    await _sync(owner, demo)
    return demo


async def _evaluate(app, now):
    state = app.state.warden
    async with state.db.sessionmaker() as db:
        out = await monitoring.evaluate(state, db, now)
        await db.commit()
    return out


async def _db(app):
    return app.state.warden.db.sessionmaker()


@pytest.fixture
def hooks(app):
    """Capture outbound HTTP from notification channels; nothing leaves the process."""
    seen: list[httpx.Request] = []
    status = {"code": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"])

    app.state.warden.http_transport = httpx.MockTransport(handler)
    return seen, status


# -- metric store -------------------------------------------------------------------------


async def test_metrics_recorded_only_while_a_consumer_is_enabled(demo, owner, app):
    async with await _db(app) as db:
        assert await db.scalar(select(func.count()).select_from(MetricSeries)) == 0
    assert (await owner.get("/api/metrics")).status_code == 409

    await owner.post("/api/modules/analytics-studio/enable", json={"with_dependencies": True})
    await _sync(owner, demo)
    cat = {m["id"]: m for m in (await owner.get("/api/metrics")).json()["items"]}
    assert len(cat["device.cpu_pct"]["series"]) == 7  # online devices with statistics
    assert len(cat["device.online"]["series"]) == 8
    assert {s["label"] for s in cat["controller.reachable"]["series"]} == {demo["name"]}
    assert cat["site.clients"]["series"][0]["last_value"] > 0
    assert any("GHz" in s["label"] for s in cat["radio.tx_retries_pct"]["series"])
    assert cat["network.clients"]["series"] and all(s["is_demo"] for s in cat["device.cpu_pct"]["series"])
    assert cat["wan.latency_ms"]["series"] == []  # probe has no targets by default


async def test_query_orders_worst_first_and_limits(phase3, owner):
    r = await owner.post("/api/metrics/query", json={"metric": "device.cpu_pct", "range": "1h", "limit": 3})
    body = r.json()
    assert r.status_code == 200 and body["tier"] == "raw" and body["step"] == 60
    values = [s["last_value"] for s in body["series"]]
    assert len(values) == 3 and values == sorted(values, reverse=True) and body["total_series"] == 7
    assert body["series"][0]["points"] and body["series"][0]["summary"]["max"] >= body["series"][0]["summary"]["min"]
    one = body["series"][0]["entity_id"]
    r = await owner.post("/api/metrics/query", json={"metric": "device.cpu_pct", "entity_ids": [one]})
    assert [s["entity_id"] for s in r.json()["series"]] == [one]
    assert (await owner.post("/api/metrics/query", json={"metric": "nope"})).status_code == 404
    assert (await owner.post("/api/metrics/query", json={"metric": "device.cpu_pct", "range": "2y"})).status_code == 422


async def test_rollup_keeps_peaks_and_prunes(app, settings):
    now = utcnow().replace(minute=30, second=0, microsecond=0)
    base = now - timedelta(hours=3)
    async with await _db(app) as db:
        for i, v in enumerate([10, 50, 30, 20, 40, 90]):  # one sample a minute, 10:00..10:05 relative
            await metrics.record(db, [metrics.Sample("device.cpu_pct", "device", "d1", "Switch", v)], base + timedelta(minutes=i))
        await metrics.record(db, [metrics.Sample("device.cpu_pct", "device", "d1", "Switch", 5)], now - timedelta(days=3))
        out = await metrics.rollup(db, now)
        await db.commit()
        assert out["pruned0"] == 1  # raw older than 48 h
        five = (
            (await db.execute(select(MetricSample).where(MetricSample.tier == metrics.FIVE_MIN).order_by(MetricSample.ts))).scalars().all()
        )
        first = five[0]
        assert (first.min, first.max, first.count) == (10, 50, 5) and first.avg == pytest.approx(30)
        assert five[1].max == 90
        hourly = (await db.execute(select(MetricSample).where(MetricSample.tier == metrics.HOURLY))).scalars().all()
        assert len(hourly) == 1 and hourly[0].max == 90 and hourly[0].count == 6
        # A second run over the same period adds nothing.
        again = await metrics.rollup(db, now)
        assert again["tier1"] == 0 and again["tier2"] == 0
        # Queries over a long range read rollups and still see the peak.
        series = await db.scalar(select(MetricSeries))
        pts, step = await metrics.points(db, [series.id], "7d", now)
        assert step == 3600 and max(p[3] for p in pts[series.id]) == 90


# -- dashboards ---------------------------------------------------------------------------


async def test_dashboards_from_template_and_editing(phase3, owner):
    templates = (await owner.get("/api/analytics/templates")).json()["items"]
    assert {t["id"] for t in templates} >= {"network", "devices", "clients", "wifi", "wan"}
    r = await owner.post("/api/analytics/dashboards", json={"template": "devices"})
    assert r.status_code == 201
    d = r.json()
    assert d["name"] == "Devices" and d["template"] == "devices" and len(d["widgets"]) == 5

    widgets = d["widgets"][:2] + [{"metric": "site.clients", "kind": "stat", "size": "third", "title": "Clients"}]
    r = await owner.patch(f"/api/analytics/dashboards/{d['id']}", json={"name": "My devices", "widgets": widgets, "default_range": "7d"})
    assert r.status_code == 200 and r.json()["widgets"][2]["id"]  # ids assigned server-side
    assert (await owner.get(f"/api/analytics/dashboards/{d['id']}")).json()["default_range"] == "7d"

    bad = await owner.post("/api/analytics/dashboards", json={"name": "x", "widgets": [{"metric": "made.up"}]})
    assert bad.status_code == 422
    assert (await owner.post("/api/analytics/dashboards", json={"template": "nope"})).status_code == 422
    assert (await owner.post("/api/analytics/dashboards", json={})).status_code == 422
    assert (await owner.delete(f"/api/analytics/dashboards/{d['id']}")).status_code == 204
    actions = [a["action"] for a in (await owner.get("/api/audit")).json()["records"]]
    assert {"analytics.dashboard.create", "analytics.dashboard.update", "analytics.dashboard.delete"} <= set(actions)


async def test_phase3_permissions(phase3, owner, app):
    assert {P.ANALYTICS_EDIT, P.MONITORING_MANAGE} <= ROLES["operator"]
    assert P.NOTIFICATIONS_MANAGE not in ROLES["operator"] and P.NOTIFICATIONS_MANAGE in ROLES["admin"]
    await owner.post("/api/users", json={"username": "vic", "password": PASSWORD, "roles": ["viewer"]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-warden-csrf": "1"}) as v:
        await v.post("/api/auth/login", json={"username": "vic", "password": PASSWORD})
        assert (await v.get("/api/analytics/dashboards")).status_code == 200
        assert (await v.post("/api/metrics/query", json={"metric": "site.clients"})).status_code == 200
        assert (await v.post("/api/analytics/dashboards", json={"template": "wan"})).status_code == 403
        assert (await v.post("/api/monitoring/rules", json={})).status_code in (403, 422)
        assert (await v.put("/api/wan/probe", json={"targets": []})).status_code == 403
        assert (await v.get("/api/notifications/channels")).status_code == 200
        assert (await v.post("/api/notifications/channels", json={})).status_code in (403, 422)


# -- monitoring ---------------------------------------------------------------------------


async def test_builtin_rule_fires_acks_and_resolves(phase3, owner, app):
    rules = (await owner.get("/api/monitoring/rules")).json()["items"]
    assert {r["name"] for r in rules if r["builtin"]} >= {"Device offline", "Controller unreachable", "High CPU"}
    offline = next(d for d in (await owner.get("/api/inventory/devices")).json()["items"] if d["state"] != "ONLINE")

    now = utcnow()
    assert (await _evaluate(app, now))["pending"] == 1
    active = (await owner.get("/api/monitoring/alerts")).json()
    assert active["counts"]["pending"] == 1 and active["counts"]["firing"] == 0
    assert (await _evaluate(app, now + timedelta(seconds=125)))["fired"] == 1
    active = (await owner.get("/api/monitoring/alerts")).json()
    alert = active["items"][0]
    assert alert["state"] == "firing" and alert["entity_id"] == offline["id"] and alert["summary"].endswith("is down")
    assert active["counts"]["critical"] == 1

    r = await owner.post(f"/api/monitoring/alerts/{alert['id']}/ack")
    assert r.status_code == 200 and r.json()["acknowledged_by"] == "owner"
    assert (await owner.get("/api/monitoring/alerts")).json()["counts"]["unacknowledged"] == 0

    # Device history shows the alert as an event.
    async with await _db(app) as db:
        ev = await db.scalar(select(EventRecord).where(EventRecord.type == "monitoring.alert.firing"))
        assert ev.entity_id == offline["id"] and ev.severity == "critical"

    sim = app.state.warden.simulator
    next(d for d in sim.devices if d["name"] == offline["name"])["state"] = "ONLINE"
    await _sync(owner, phase3)
    assert (await _evaluate(app, utcnow()))["resolved"] == 1
    hist = (await owner.get("/api/monitoring/alerts", params={"state": "resolved"})).json()["items"]
    assert hist[0]["resolution"] == "recovered"
    assert (await owner.post(f"/api/monitoring/alerts/{alert['id']}/ack")).status_code == 409


async def test_stale_series_resolve_as_no_data(phase3, owner, app):
    now = utcnow()
    await _evaluate(app, now)
    await _evaluate(app, now + timedelta(seconds=125))
    assert (await _evaluate(app, now + timedelta(minutes=10)))["resolved"] == 1
    assert (await owner.get("/api/monitoring/alerts", params={"state": "resolved"})).json()["items"][0]["resolution"] == "no_data"


async def test_custom_rule_maintenance_and_notifications(phase3, owner, app, hooks):
    seen, status = hooks
    ch = await owner.post(
        "/api/notifications/channels",
        json={
            "name": "Ops hook",
            "kind": "webhook",
            "min_severity": "info",
            "webhook": {"url": "https://hooks.example/abc/secret-path", "signing_secret": "s3cret"},
        },
    )
    assert ch.status_code == 201, ch.text
    channel = ch.json()
    assert channel["display"] == "https://hooks.example" and "secret-path" not in json.dumps(channel)

    devices = (await owner.get("/api/inventory/devices")).json()["items"]
    quiet = next(d for d in devices if d["name"] == "Core Aggregation")
    win = await owner.post("/api/monitoring/maintenance", json={"name": "Core swap", "duration_minutes": 30, "entity_ids": [quiet["id"]]})
    assert win.status_code == 201 and win.json()["state"] == "active"

    r = await owner.post(
        "/api/monitoring/rules",
        json={
            "name": "Any CPU",
            "metric": "device.cpu_pct",
            "comparator": "gt",
            "threshold": 0,
            "severity": "warning",
            "channel_ids": [channel["id"]],
        },
    )
    assert r.status_code == 201
    rule = r.json()
    out = await _evaluate(app, utcnow())
    assert out["fired"] == 7

    alerts = [a for a in (await owner.get("/api/monitoring/alerts")).json()["items"] if a["rule_id"] == rule["id"]]
    assert sum(a["silenced"] for a in alerts) == 1 and next(a for a in alerts if a["silenced"])["entity_id"] == quiet["id"]
    assert len(seen) == 6  # the silenced alert notifies nobody
    req = seen[0]
    sig = "sha256=" + hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-warden-signature"] == sig and str(req.url) == "https://hooks.example/abc/secret-path"
    payload = json.loads(req.content)
    assert payload["kind"] == "firing" and payload["alert"]["is_demo"] is True

    # Disabling the rule resolves its alerts; the resolution notifies too.
    seen.clear()
    await owner.patch(f"/api/monitoring/rules/{rule['id']}", json={"enabled": False})
    assert (await _evaluate(app, utcnow()))["resolved"] == 7
    assert len(seen) == 6 and json.loads(seen[0].content)["kind"] == "resolved"

    # Failed deliveries are recorded on the channel.
    status["code"] = 500
    t = (await owner.post(f"/api/notifications/channels/{channel['id']}/test")).json()
    assert t["status"] == "failed" and "500" in t["error"] and t["channel"]["last_status"] == "failed"
    deliveries = (await owner.get("/api/notifications/deliveries", params={"channel_id": channel["id"]})).json()["items"]
    assert deliveries[0]["kind"] == "test" and {d["kind"] for d in deliveries} >= {"firing", "resolved", "test"}

    ended = await owner.post(f"/api/monitoring/maintenance/{win.json()['id']}/end")
    assert ended.json()["state"] == "ended"


async def test_notifications_off_means_no_delivery(phase3, owner, app, hooks):
    seen, _ = hooks
    await owner.post("/api/notifications/channels", json={"name": "h", "kind": "webhook", "webhook": {"url": "https://hooks.example/x"}})
    await owner.post("/api/modules/notifications/disable", json={})
    await owner.post(
        "/api/monitoring/rules", json={"name": "r", "metric": "site.clients", "comparator": "gt", "threshold": 0, "severity": "critical"}
    )
    assert (await _evaluate(app, utcnow()))["fired"] == 1
    assert seen == []


async def test_ntfy_and_email_channels(phase3, owner, app, hooks):
    seen, _ = hooks
    sent = []

    async def fake_smtp(config, password, message):
        sent.append((config, password, message))

    app.state.warden.smtp_send = fake_smtp
    r = await owner.post(
        "/api/notifications/channels",
        json={"name": "Phone", "kind": "ntfy", "ntfy": {"server": "https://ntfy.example/", "topic": "warden-alerts", "token": "tk_1"}},
    )
    assert r.status_code == 201 and r.json()["display"] == "ntfy.example/war…"
    t = (await owner.post(f"/api/notifications/channels/{r.json()['id']}/test")).json()
    assert t["status"] == "sent"
    assert str(seen[0].url) == "https://ntfy.example/warden-alerts" and seen[0].headers["authorization"] == "Bearer tk_1"

    r = await owner.post(
        "/api/notifications/channels",
        json={
            "name": "Mail",
            "kind": "email",
            "email": {
                "host": "smtp.example",
                "username": "warden",
                "password": "pw",
                "from_addr": "warden@example.com",
                "to": ["me@example.com"],
            },
        },
    )
    assert r.status_code == 201, r.text
    mail = r.json()
    assert mail["has_secret"] and "pw" not in json.dumps(mail)
    assert (await owner.post(f"/api/notifications/channels/{mail['id']}/test")).json()["status"] == "sent"
    assert sent[0][1] == "pw" and sent[0][2]["To"] == "me@example.com"

    # A blank password on edit keeps the stored one.
    upd = await owner.patch(
        f"/api/notifications/channels/{mail['id']}",
        json={"email": {"host": "smtp2.example", "from_addr": "warden@example.com", "to": ["me@example.com"], "username": "warden"}},
    )
    assert upd.status_code == 200 and upd.json()["config"]["host"] == "smtp2.example"
    await owner.post(f"/api/notifications/channels/{mail['id']}/test")
    assert sent[1][1] == "pw"

    bad = await owner.post("/api/notifications/channels", json={"name": "x", "kind": "webhook", "webhook": {"url": "ftp://nope"}})
    assert bad.status_code == 422
    assert (await owner.post("/api/notifications/channels", json={"name": "x", "kind": "ntfy"})).status_code == 422

    assert (await owner.delete(f"/api/notifications/channels/{mail['id']}")).status_code == 204
    async with await _db(app) as db:
        assert await db.scalar(select(func.count()).select_from(Credential).where(Credential.kind == "notify.email")) == 0


async def test_deleted_builtin_rule_stays_deleted(phase3, owner, app):
    rules = (await owner.get("/api/monitoring/rules")).json()["items"]
    cpu = next(r for r in rules if r["name"] == "High CPU")
    assert (await owner.delete(f"/api/monitoring/rules/{cpu['id']}")).status_code == 204
    await _evaluate(app, utcnow())
    names = {r["name"] for r in (await owner.get("/api/monitoring/rules")).json()["items"]}
    assert "High CPU" not in names
    async with await _db(app) as db:
        assert await db.scalar(select(func.count()).select_from(AlertRule).where(AlertRule.builtin_key == "device_cpu")) == 0
        assert await db.scalar(select(func.count()).select_from(Alert)) >= 0


# -- WAN ----------------------------------------------------------------------------------


async def test_wan_overview_and_probe(phase3, owner, app):
    body = (await owner.get("/api/wan")).json()
    assert {w["name"] for w in body["wans"]} == {"Primary (WAN1)", "Backup (WAN2)"}
    assert body["gateways"][0]["name"] == "Gateway" and body["probe"]["targets"] == []

    run = (await owner.post("/api/wan/probe/run")).json()
    assert run["summary"] == {"skipped": "no probe targets configured"}

    assert (await owner.put("/api/wan/probe", json={"targets": [{"host": "https://bad/x"}]})).status_code == 422
    dup = {"targets": [{"host": "one.one.one.one", "port": 443}, {"host": "ONE.one.one.one", "port": 443}]}
    assert (await owner.put("/api/wan/probe", json=dup)).status_code == 422

    results = iter([10.0, None, 12.0, None, None, None])

    async def fake_connect(host, port, _timeout):
        return next(results)

    app.state.warden.probe_connect = fake_connect
    r = await owner.put(
        "/api/wan/probe", json={"targets": [{"host": "1.1.1.1", "label": "Cloudflare"}, {"host": "dns.example", "port": 53}]}
    )
    assert r.status_code == 200
    run = (await owner.post("/api/wan/probe/run")).json()
    assert run["status"] == "succeeded"
    probe = (await owner.get("/api/wan")).json()["probe"]
    cf, ex = probe["results"]
    assert (cf["label"], cf["latency_ms"], round(cf["loss_pct"], 1)) == ("Cloudflare", 11.0, 33.3)
    assert ex["latency_ms"] is None and ex["loss_pct"] == 100.0 and not ex["stale"]
