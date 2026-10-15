from __future__ import annotations

import pytest

from draftcoach.draft_order import SEQUENCE, ManualDraft


def test_sequence_is_the_standard_tournament_draft():
    assert len(SEQUENCE) == 20
    kinds = "".join("b" if s.kind == "ban" else "p" for s in SEQUENCE)
    assert kinds == "bbbbbb" + "pppppp" + "bbbb" + "pppp"
    teams = "".join(s.team for s in SEQUENCE)
    assert teams == "BRBRBR" + "BRRBBR" + "RBRB" + "RBBR"
    assert [s.label for s in SEQUENCE if s.kind == "pick"] == ["B1", "R1", "R2", "B2", "B3", "R3", "R4", "B4", "B5", "R5"]


def test_manual_draft_walks_the_sequence_with_undo_and_redo():
    draft = ManualDraft(side="blue")
    assert draft.current().label == "BB1" and draft.current().team == "B"
    draft.act("rumble")
    assert draft.bans("B") == ["Rumble"] and draft.current().label == "RB1"
    with pytest.raises(ValueError):
        draft.act("Rumble")                      # already taken
    with pytest.raises(ValueError):
        draft.act("not a champion")
    for c in ["Vi", "Yone", "Jinx", "Aurora", "Kalista"]:
        draft.act(c)
    assert draft.current().kind == "pick" and draft.current().label == "B1"
    draft.act("Ornn", role="Top")
    assert draft.picks("B")[0].role == "Top"
    undone = draft.undo()
    assert undone.champion == "Ornn" and draft.current().label == "B1"
    draft.redo_last()
    assert draft.picks("B")[0].champion == "Ornn" and not draft.redo
    draft.set_role(6, "Mid")
    assert draft.picks("B")[0].role == "Mid"
    draft.reset("red")
    assert draft.side == "red" and draft.our_letter == "R" and not draft.actions


def test_manual_draft_completes_after_twenty_actions():
    draft = ManualDraft()
    names = ["Aatrox", "Ahri", "Akali", "Alistar", "Amumu", "Anivia", "Annie", "Aphelios", "Ashe", "Azir",
             "Bard", "Blitzcrank", "Brand", "Braum", "Caitlyn", "Camille", "Cassiopeia", "Corki", "Darius", "Diana"]
    for n in names:
        draft.act(n)
    assert draft.is_complete() and draft.current() is None
    with pytest.raises(ValueError):
        draft.act("Draven")
    data = draft.to_dict()
    assert data["complete"] and data["current"] is None and len(data["actions"]) == 20
    assert data["draft_type"] == "tournament" and data["actions"][6]["label"] == "B1"
