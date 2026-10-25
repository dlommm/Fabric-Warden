"""Traffic & Flow Analytics: NetFlow v5/v9/IPFIX decoding, rollups, summary API and demo feed."""

import asyncio
import socket
import struct

import pytest

from warden.flows import Decoder, Flow, FlowDecodeError, encode_ipfix, encode_v5, scope

V4 = Flow("10.0.20.105", "10.0.30.101", 6, 52114, 445, 1500, 3)
V6 = Flow("2001:db8:20::113", "2001:db8:ffff::443", 6, 51000, 443, 900, 2)


def test_v5_header_sampling_and_ipv4_only():
    d = Decoder()
    flows = d.decode(encode_v5([V4], sampling=256), "10.0.1.1")
    assert flows == [Flow("10.0.20.105", "10.0.30.101", 6, 52114, 445, 1500, 3, 256)]
    assert flows[0].port == 445 and flows[0].ip_version == 4
    # Unsampled header (0) is "not reported", not "1 in 1".
    assert d.decode(encode_v5([V4]), "10.0.1.2")[0].sampling == 0


@pytest.mark.parametrize("version", [9, 10])
def test_template_formats_ipv4_ipv6_and_sampling_options(version):
    d = Decoder()
    flows = d.decode(encode_ipfix([V4, V6], sampling=512, version=version), "10.0.1.1")
    assert [(f.src, f.dst, f.port, f.bytes, f.packets, f.sampling) for f in flows] == [
        ("10.0.20.105", "10.0.30.101", 445, 1500, 3, 512),
        ("2001:db8:20::113", "2001:db8:ffff::443", 443, 900, 2, 512),
    ]
    ex = d.exporters[("10.0.1.1", 1)]
    assert ex.version == version and ex.sampling == 512 and "sourceIPv6Address" in ex.fields_seen()
    # Later exports without templates decode from the remembered ones.
    assert len(d.decode(encode_ipfix([V4], version=version, with_templates=False), "10.0.1.1")) == 1


def test_data_before_template_is_counted_not_guessed():
    d = Decoder()
    assert d.decode(encode_ipfix([V4], with_templates=False), "10.0.1.9") == []
    assert d.exporters[("10.0.1.9", 1)].undecoded == 1
    # Templates are per exporter: another address's template does not apply.
    d.decode(encode_ipfix([V4]), "10.0.1.1")
    assert d.decode(encode_ipfix([V4], with_templates=False), "10.0.1.9") == []


def test_malformed_input_raises_cleanly():
    d = Decoder()
    good = encode_ipfix([V4])
    for bad in (b"", b"\x00\x07", good[:-3], good[:20], struct.pack("!HH", 10, 4) + b"\0" * 12):
        with pytest.raises((FlowDecodeError, struct.error, IndexError, ValueError)):
            d.decode(bad, "10.0.1.1")
    # A template whose records have no length would loop forever; it is refused.
    zero = struct.pack("!HHHH", 256, 1, 8, 0)
    pkt = struct.pack("!HHIII", 10, 16 + 4 + len(zero), 0, 0, 1) + struct.pack("!HH", 2, 4 + len(zero)) + zero
    with pytest.raises(FlowDecodeError):
        d.decode(pkt, "10.0.1.1")


def test_ipfix_variable_length_and_enterprise_fields():
    # Template: enterprise field (variable length) then source/destination IPv4 and octets.
    fields = struct.pack("!HHI", 0x8000 | 1, 65535, 6876) + struct.pack("!HHHHHH", 8, 4, 12, 4, 1, 4)
    tpl = struct.pack("!HH", 256, 4) + fields
    rec = b"\x03abc" + bytes([10, 0, 0, 1, 10, 0, 0, 2]) + (77).to_bytes(4, "big")
    sets = struct.pack("!HH", 2, 4 + len(tpl)) + tpl + struct.pack("!HH", 256, 4 + len(rec)) + rec
    pkt = struct.pack("!HHIII", 10, 16 + len(sets), 0, 0, 7) + sets
    [f] = Decoder().decode(pkt, "10.0.1.1")
    assert (f.src, f.dst, f.bytes, f.port) == ("10.0.0.1", "10.0.0.2", 77, None)


def test_scope():
    assert scope("10.0.30.101") == "private" and scope("fd00::1") == "private" and scope("1.1.1.1") == "public"


@pytest.fixture
async def flows_on(demo, owner):
    r = await owner.post("/api/modules/flows/enable", json={"with_dependencies": True})
    assert r.status_code == 200, r.text
    return demo


async def test_udp_collector_rolls_up_and_matches_inventory(flows_on, owner, app):
    from warden.flows import Collector

    col = Collector(app.state.warden)
    await col.start("127.0.0.1", 0)
    host, port = col.listening.removeprefix("udp://").rsplit(":", 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(3):
        s.sendto(encode_ipfix([V4, V6], sampling=10), (host, int(port)))
    s.sendto(b"not netflow", (host, int(port)))
    s.close()
    for _ in range(100):
        if col.datagrams >= 4:
            break
        await asyncio.sleep(0.02)
    assert (col.datagrams, col.malformed, col.flows) == (4, 1, 6)
    assert await col.flush() == 2  # three exports of the same two conversations, one row each
    await col.stop()
    data = (await owner.get("/api/flows/summary", params={"hours": 1})).json()
    assert data["totals"]["bytes"] == 3 * (1500 + 900) * 10 and data["totals"]["flows"] == 6
    smb = next(c for c in data["conversations"] if c["port"] == 445)
    assert smb["src"]["entity"]["name"] == "workstation" and smb["dst"]["entity"]["name"] == "unraid-tower"
    assert smb["protocol"] == "TCP" and smb["bytes"] == 3 * 1500 * 10
    assert data["by_ip_version"] == {"IPv4": 45000, "IPv6": 27000} and data["sampling"]["rates"] == [10]


async def test_allowed_exporters(flows_on, app):
    from warden.flows import Collector

    st = app.state.warden
    st.settings.flow_allowed_exporters = ["10.0.1.0/24"]
    try:
        col = Collector(st)
        assert col.submit(encode_ipfix([V4]), "192.168.9.9") == 0 and col.rejected == 1
        assert col.submit(encode_ipfix([V4]), "10.0.1.1") == 1
    finally:
        st.settings.flow_allowed_exporters = []


async def test_demo_summary_status_and_removal(flows_on, owner):
    assert (await owner.post("/api/flows/demo")).status_code == 201
    data = (await owner.get("/api/flows/summary")).json()
    assert data["has_demo"] and data["sampling"]["rates"] == [512] and data["totals"]["bytes"] > 0
    names = {t["entity"]["name"] for t in data["talkers"] if t["entity"]}
    assert {"unraid-tower", "workstation", "tv-living"} <= names
    assert "IPv6" in data["by_ip_version"] and any(p["port"] == 445 for p in data["ports"])
    one = (await owner.get("/api/flows/summary", params={"ip": "10.0.40.118"})).json()
    assert {c["dst"]["ip"] for c in one["conversations"]} == {"10.0.30.111"}
    st = (await owner.get("/api/flows/status")).json()
    # Demo exports never show up as a live exporter or in the receiver's counters.
    assert st["enabled"] is False and st["exporters"] == [] and st["flows"] == 0
    assert (await owner.delete("/api/flows/demo")).json()["removed"] > 0
    assert (await owner.get("/api/flows/summary")).json()["totals"]["bytes"] == 0


async def test_flows_require_module(demo, owner):
    assert (await owner.get("/api/flows/summary")).status_code == 409
