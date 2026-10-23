"""Ste's extra draft options must remain real, scored alternatives."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from league_flex_core import ROLES, _attach_role_alternatives, role_option_limit


class SteMidVarietyTests(unittest.TestCase):
    def test_only_ste_mid_has_expanded_display_capacity(self):
        self.assertEqual(role_option_limit("Mid", 286592835850076162), 5)
        self.assertEqual(role_option_limit("ADC", 286592835850076162), 3)
        self.assertEqual(role_option_limit("Mid", 111), 3)

    def test_confirmed_ste_champions_are_in_persisted_pool(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "loadfiles" / "flexers-team.json").read_text(encoding="utf-8"))
        ste = next(p for p in data["players"] if p["user_id"] == 286592835850076162)
        entries = ste["pools"]["Mid"]
        self.assertGreaterEqual(len(entries), 10)
        self.assertEqual({r["champion"] for r in entries if r.get("live_confirmed")}, {"Vex", "Akali"})
        confirmations = json.loads((root / "data" / "league_player_confirmations.json").read_text(encoding="utf-8"))
        self.assertEqual(set(confirmations["players"]["286592835850076162"]["Mid"]), {"Vex", "Akali"})

    def test_ste_gets_four_viable_mid_swaps_but_others_get_two(self):
        champs = dict(zip(ROLES, ["Camille", "Lee Sin", "Vex", "Aphelios", "Thresh"]))
        players = {role: {"user_id": i + 50} for i, role in enumerate(ROLES)}
        players["Mid"] = {"user_id": 286592835850076162}
        base = {
            "champions": champs, "players": players, "score": 50.0,
            "team_plan": "balanced", "team_plan_score": 8.0,
            "credible_mixed_damage": True, "mid_jungle_score": 8.0,
            "bot_lane_score": 8.0
        }
        variants = [dict(base, champions=dict(champs, Mid=mid), score=49 - i * 0.2)
                    for i, mid in enumerate(["Akali", "Ahri", "Orianna", "Galio", "Viktor"])]
        with patch("league_flex_core._linked_bot_lane_packages", return_value=[]), \
             patch("league_flex_core._linked_mid_jungle_packages", return_value=[]):
            result = _attach_role_alternatives([dict(base)], variants)
            self.assertEqual(len(result[0]["role_alternatives"]["Mid"]), 4)
            other = dict(base, players={**players, "Mid": {"user_id": 999}})
            other_variants = [dict(row, players=other["players"]) for row in variants]
            result = _attach_role_alternatives([other], other_variants)
            self.assertEqual(len(result[0]["role_alternatives"]["Mid"]), 2)


if __name__ == "__main__":
    unittest.main()
