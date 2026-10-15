from __future__ import annotations

import importlib
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
proxy = importlib.import_module("intent_hud_proxy")


def test_proxy_replaces_old_hud_pick_with_intent(monkeypatch):
    base = {
        "state": {"active": {"kind": "pick", "ours": True}},
        "guidance": {"picks": [{"champion": "Urgot"}]},
        "local": {"can_act": True, "suggestion": {"champion": "Urgot"}},
    }
    intent = {"active": True, "guidance": {"picks": [{"champion": "Camille", "champion_id": 164, "role": "Top", "why": ["shared backend"]}], "bans": []}}

    def fake(url, timeout=12.0):
        return base.copy() if url.endswith("/api/state") else intent

    monkeypatch.setattr(proxy, "_json", fake)
    merged = proxy.merged_state()
    assert merged["guidance"]["picks"][0]["champion"] == "Camille"
    assert merged["local"]["suggestion"]["champion"] == "Camille"


def test_proxy_hides_hud_when_intent_is_gated(monkeypatch):
    base = {"state": {"active": {"kind": "pick", "ours": True}}, "guidance": {"picks": [{"champion": "Urgot"}]},
            "local": {"can_act": True, "suggestion": {"champion": "Urgot"}}}

    def fake(url, timeout=12.0):
        return base.copy() if url.endswith("/api/state") else {"active": False, "disabled": True}

    monkeypatch.setattr(proxy, "_json", fake)
    merged = proxy.merged_state()
    assert merged["guidance"] is None
    assert merged["local"]["can_act"] is False
    assert merged["local"]["suggestion"] is None
