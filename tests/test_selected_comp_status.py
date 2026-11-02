"""The selected Discord comp must be visible in both app surfaces while idle."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_coach_shows_shared_comp_status_outside_champion_select():
    html = (ROOT / "draftcoach/static/coach.html").read_text(encoding="utf-8")
    assert 'id="team-choice-status"' in html
    assert 'role="status"' in html
    assert "function renderSelectedTeamComp()" in html
    assert 'const chosen = intentControl && intentControl.chosen;' in html
    assert "renderSelectedTeamComp();\n    if (latest) render(latest);" in html
    assert "No Discord comp synced" in html
    assert "Adaptive plan: player roles fixed" in html


def test_floating_panel_status_works_even_while_waiting_for_champ_select():
    html = (ROOT / "draftcoach/static/overlay.html").read_text(encoding="utf-8")
    assert 'id="team-choice-status"' in html
    assert 'const chosen = panelControl && panelControl.chosen;' in html
    assert 'panelControl = data.control;\n      renderSelectedTeamComp();' in html
    assert "No Discord comp synced" in html
    assert "Adaptive: roles fixed" in html


def test_discord_confirmation_does_not_claim_every_app_acknowledged():
    py = (ROOT / "cogs/league_flex.py").read_text(encoding="utf-8")
    assert "sent to the shared Draft Coach room" in py
    assert "No Discord comp synced" in py
    assert "selected in everyone's Draft Coach" not in py
