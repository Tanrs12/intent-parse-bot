"""Browser regressions for the static UI; no server or League client is started.

Run with Playwright's Chromium installed. API and WebSocket boundaries are fixtures;
the actual HTML, scripts, styles, image loading and button handlers run in Chromium.
"""
import base64
import json
import mimetypes
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

pw = pytest.importorskip("playwright.sync_api")
STATIC = Path(__file__).resolve().parents[1] / "draftcoach" / "static"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1XkAAAAASUVORK5CYII=")


def draft():
    roles = ["Top", "Jungle", "Mid", "ADC", "Support"]
    names = ["Ornn", "Vi", "Ahri", "Kai'Sa", "Nami"]
    ids = [516, 254, 103, 145, 267]
    players = ["Cian", "Will", "Tom", "Aristo", "Sasha"]
    rows = [dict(role=r, champion=n, champion_id=i, player=p, slot=f"B{k+1}",
                 status="next" if k == 4 else "locked", score=75, comp_score=70,
                 familiarity="comfort") for k, (r, n, i, p) in enumerate(zip(roles, names, ids, players))]
    seats = [dict(cell_id=k, role=r, display_name=p, is_local=k == 4,
                  locked=n if k < 4 else None, hover=None, pool_source="player")
             for k, (r, n, p) in enumerate(zip(roles, names, players))]
    return dict(status=dict(connection="simulation"),
                state=dict(source="simulation", side="blue", phase="BAN_PICK", our_team=seats,
                           enemy_team=[], our_bans=[], enemy_bans=[], deadline=time.time() + 17,
                           active=dict(kind="pick", ours=True, cell_ids=[4])),
                guidance=dict(picks=[rows[4]], bans=[], pick_order=rows, roles_now=["Support"],
                              comps=[dict(champions=dict(zip(roles, names)), champion_ids=dict(zip(roles, ids)),
                                          team_plan="Protect the carry", score=75)]),
                local=dict(can_act=True, kind="pick", name="Sasha", role="Support",
                           suggestion=dict(rows[4], kind="pick")), draft={})


@pytest.fixture
def browser_page():
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except pw.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        page = browser.new_page(viewport=dict(width=430, height=480))
        errors, posts = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: print(m.text) if m.type == "error" else None)
        def route(req):
            path = urlparse(req.request.url).path
            if req.request.method == "POST":
                posts.append((path, req.request.post_data_json))
                req.fulfill(json={})
            elif path.startswith("/static/"):
                file = STATIC / path.removeprefix("/static/")
                mime = {".js": "text/javascript", ".css": "text/css"}.get(file.suffix)
                req.fulfill(path=file, content_type=mime or mimetypes.guess_type(file)[0] or "text/plain")
            elif path in ("/overlay", "/"):
                req.fulfill(path=STATIC / ("overlay.html" if path == "/overlay" else "coach.html"), content_type="text/html")
            elif path.startswith(("/api/champion-icon/", "/api/art/centered/", "/api/art/tile/")):
                name = {"516": "Ornn", "254": "Vi", "103": "Ahri", "145": "Kaisa", "267": "Nami"}.get(path.rsplit("/", 1)[-1])
                file = STATIC.parents[1] / "assets" / "league_champions" / f"{name}.png"
                req.fulfill(body=file.read_bytes() if file.is_file() else PNG, content_type="image/png")
            elif path == "/api/art/index":
                req.fulfill(json=dict(porcelain=[], gesture=[], slugs={}))
            elif path == "/api/champions":
                req.fulfill(json=dict(champions={}))  # IDs must also come from guidance.
            elif path == "/api/control":
                req.fulfill(json=dict(control={}, identities=[]))
            elif path == "/api/guidance":
                req.fulfill(json=dict(active=False))
            else:
                req.fulfill(json={})
        page.route("**/*", route)
        page.add_init_script("""window.WebSocket = class {
          constructor() { window.__socket = this; }
        }; window.deliver = s => window.__socket.onmessage({data: JSON.stringify(s)});""")
        yield page, errors, posts
        browser.close()


def show(page, snap, path="/overlay?host=webview"):
    page.goto("http://flexers.test" + path)
    page.wait_for_function("!!(window.__socket && window.__socket.onmessage)", timeout=5000)
    page.evaluate("s => window.deliver(s)", snap)


def test_portraits_load_when_ids_are_only_in_team_comp(browser_page):
    page, errors, _ = browser_page
    show(page, draft())
    page.wait_for_function("[...document.querySelectorAll('#order-list img')].filter(i => i.complete && i.naturalWidth > 0).length === 5", timeout=5000)
    assert page.evaluate("Draft.idFor('Ornn')") == 516
    assert page.locator("#order-list").inner_text().count("Ornn") == 1
    assert errors == []


def test_simulated_local_contract_exposes_working_lock_button(browser_page):
    page, errors, posts = browser_page
    show(page, draft())
    button = page.get_by_role("button", name="Lock in Nami", exact=True)
    pw.expect(button).to_be_enabled()
    assert button.bounding_box()["y"] + button.bounding_box()["height"] <= 480
    button.click()
    # The click carries the action it was drawn for (stale-click guard), plus the champion.
    assert [(path, body["champion"], body.get("kind")) for path, body in posts] == [("/api/client/lock", "Nami", "pick")]
    assert errors == []


def test_teammate_turn_has_plain_owner_and_no_actions(browser_page):
    page, errors, _ = browser_page
    snap = draft()
    snap["state"]["our_team"][4]["is_local"] = False
    snap["state"]["our_team"][0]["is_local"] = True
    snap["local"] = dict(client=True, can_act=False, role="Top", name="Cian")
    show(page, snap)
    pw.expect(page.locator("#lead")).to_have_text("Nami")
    pw.expect(page.locator("#actions")).to_be_hidden()
    assert errors == []


def test_unavailable_simulation_explains_disabled_action(browser_page):
    page, errors, _ = browser_page
    snap = draft()
    snap["local"]["can_act"] = False
    show(page, snap)
    pw.expect(page.get_by_role("button", name="Lock in Nami", exact=True)).to_be_disabled()
    pw.expect(page.locator("#action-note")).to_contain_text("Demo mode")
    assert errors == []


def test_coach_local_action_and_art_match_without_player_id(browser_page):
    page, errors, posts = browser_page
    page.set_viewport_size(dict(width=1440, height=1000))
    show(page, draft(), "/")
    button = page.get_by_role("button", name="Lock in Nami", exact=True)
    pw.expect(button).to_be_enabled()
    page.wait_for_function("document.getElementById('hero-art').naturalWidth > 0")
    assert page.locator("body.graphite").count() == 0
    button.click()
    assert posts == [("/api/client/lock", {"champion": "Nami"})]
    assert errors == []


def test_panel_keeps_five_and_action_in_view(browser_page, tmp_path):
    page, errors, _ = browser_page
    print("UI screenshots:", tmp_path)
    show(page, draft())
    page.wait_for_function("document.getElementById('art').naturalWidth > 0")
    for selector in ("#accept", "#order-list li:last-child", ".ov-foot"):
        box = page.locator(selector).bounding_box()
        assert box and box["y"] + box["height"] <= 480
    assert page.evaluate("document.documentElement.scrollWidth <= 430")
    page.screenshot(path=tmp_path / "panel-porcelain.png")
    page.locator("#theme").click()
    pw.expect(page.locator("body")).to_have_class("fd-overlay graphite")
    page.screenshot(path=tmp_path / "panel-graphite.png")
    assert errors == []


def test_icon_failure_uses_tile_and_never_initials(browser_page):
    page, errors, _ = browser_page
    page.route("**/api/champion-icon/*", lambda r: r.fulfill(status=404))
    show(page, draft())
    page.wait_for_function("[...document.querySelectorAll('#order-list img')].every(i => i.naturalWidth > 0 && i.src.includes('/api/art/tile/'))")
    assert page.locator("#hero-portrait").inner_text() == ""
    assert errors == []


def test_stale_swap_is_never_actionable(browser_page):
    page, errors, posts = browser_page
    snap = draft()
    snap["status"]["stale"] = True
    snap["local"]["incoming_swap"] = dict(can_accept=True, trade_id=12, champion="Ahri", from_player="Tom")
    show(page, snap)
    assert page.locator("#actions button:enabled").count() == 0
    assert posts == []
    assert errors == []


def test_missing_suggestion_cannot_lock_another_roles_top_pick(browser_page):
    page, errors, _ = browser_page
    snap = draft()
    snap["local"]["suggestion"] = None
    show(page, snap)
    assert page.locator("#actions button:enabled").count() == 0
    assert errors == []


def test_explicit_dark_choice_survives_reload_and_applies_to_coach(browser_page):
    page, errors, _ = browser_page
    # The obsolete automatic dark default should not force a new preference.
    page.add_init_script("localStorage.setItem('fd-overlay-theme', 'graphite')")
    show(page, draft())
    assert page.locator("body.graphite").count() == 0
    page.locator("#theme").click()
    pw.expect(page.locator("body")).to_have_class("fd-overlay graphite")
    assert page.evaluate("localStorage.getItem('fd-theme-preference-v1')") == "graphite"
    page.reload()
    pw.expect(page.locator("body")).to_have_class("fd-overlay graphite")
    # Both surfaces share the same saved setting.
    show(page, draft(), "/")
    pw.expect(page.locator("body.graphite")).to_have_count(1)
    page.locator("#coach-theme").click()
    pw.expect(page.locator("body.graphite")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('fd-theme-preference-v1')") == "porcelain"
    show(page, draft())
    pw.expect(page.locator("body")).to_have_class("fd-overlay")
    assert errors == []


def test_coach_mobile_has_no_horizontal_overflow(browser_page, tmp_path):
    page, errors, _ = browser_page
    print("UI screenshots:", tmp_path)
    page.set_viewport_size(dict(width=390, height=844))
    show(page, draft(), "/")
    page.wait_for_function("document.getElementById('hero-art').naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= 390")
    page.screenshot(path=tmp_path / "coach-mobile.png", full_page=True)
    page.set_viewport_size(dict(width=1440, height=1000))
    page.screenshot(path=tmp_path / "coach-desktop.png", full_page=True)
    assert errors == []


def test_ban_turn_with_backend_object_reasons_shows_working_ban_button(browser_page):
    """Team mode: the shared backend's ban reasons are objects, not text. They once threw inside the
    reason formatter, so no overlay in a team lobby ever showed its Ban button."""
    page, errors, posts = browser_page
    snap = draft()
    ban = dict(champion="Ezreal", champion_id=81, score=3.8, tags=[],
               reasons=[dict(role="ADC", our_champion="Jinx", pick_number=1, counter_edge=4.37, games=3391)])
    snap["state"]["active"] = dict(kind="ban", ours=True, cell_ids=[0, 1, 2, 3, 4])
    for seat in snap["state"]["our_team"]:
        seat["locked"] = None
    snap["guidance"]["bans"] = [ban]
    snap["local"] = dict(can_act=True, kind="ban", name="Sasha", role="Support",
                         suggestion=dict(kind="ban", champion="Ezreal", champion_id=81, reason="beats our Jinx ADC by +4.4"))
    show(page, snap)
    button = page.get_by_role("button", name="Ban Ezreal", exact=True)
    pw.expect(button).to_be_enabled()
    pw.expect(page.locator("#panel")).to_contain_text("Strong into our Jinx (bot)")
    button.click()
    assert [(path, body["champion"], body.get("kind")) for path, body in posts] == [("/api/client/lock", "Ezreal", "ban")]
    assert errors == []
