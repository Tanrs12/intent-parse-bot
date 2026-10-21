"""Temporary, source-labeled 26.21 live-client balance priors for the Flex drafter.

These values are bounded **draft-score points**, NOT fabricated win-rate deltas.
Official patch notes supply change direction; patch-day SoloQ samples are not
large enough to certify new rankings. Worlds 2026 uses 26.20, so its pro picks
remain valid as historical draft/synergy evidence, NOT as 26.21 win rates.

The bridge expires automatically at 26.22 rather than masquerading as fresh data.
Review this module when a new patch launches.
Source: https://www.leagueoflegends.com/en-us/news/game-updates/league-of-legends-patch-26-21-notes/
"""
from datetime import date

LIVE_PATCH = "26.21"
PATCH_START = date(2026, 10, 21)
PATCH_EXPIRES = date(2026, 11, 4)

# Conservative direction / magnitude in final draft-score points.
# Intentionally avoids global ADC/enchanter shifts from item changes because
# item use and build paths are conditional and not evidenced for each player.
_PATCH_ROLE_SCORE = {
    ("Top", "Darius"): 0.20,       # Rank-5 Q cooldown down
    ("Top", "Sion"): 0.45,         # Health growth / R damage
    ("Top", "Wukong"): 0.15,       # Lane sustain compensation
    ("Top", "Yone"): -0.25,
    ("Jungle", "Lee Sin"): -0.45,  # W/R nerfs
    ("Jungle", "Nidalee"): -0.40,  # Clear / attack speed
    ("Jungle", "Rammus"): -0.45,   # Defensive W resists
    ("Jungle", "Wukong"): -0.50,   # Passive early armor
    ("Jungle", "Xin Zhao"): 0.35, # Q damage / E scaling
    ("Jungle", "Taliyah"): 0.35,   # E damage / monster multiplier
    ("Jungle", "Nunu & Willump"): 0.20,
    ("Mid", "Orianna"): 0.40,     # Q/W AP ratios
    ("Mid", "Ryze"): 0.35,        # Later Q/W ratios / movement
    ("Mid", "Azir"): 0.20,        # Mana growth
    ("Mid", "Viktor"): -0.50,     # Move speed / Q shield
    ("Mid", "Qiyana"): -0.25,
    ("Mid", "Yone"): -0.25,
    ("Mid", "Vel'Koz"): 0.25,
    ("ADC", "Jinx"): -0.30,       # Early HP / AD growth
    ("ADC", "Caitlyn"): -0.45,   # Weaker scaling Headshots, stronger Q
    ("ADC", "Tristana"): -0.40,  # Q ranks 1-4 AS
    ("ADC", "Miss Fortune"): 0.30, # Buffs partially offset ranged Hubris nerf
    ("Support", "Vel'Koz"): 0.25,
    ("Support", "Zyra"): 0.20,
    ("Support", "Taric"): 0.15,
}


def live_patch_active(on_date: date | None = None) -> bool:
    today = on_date if on_date is not None else date.today()
    return PATCH_START <= today < PATCH_EXPIRES


def live_patch_role_adjustment(role: str, champion: str, *, on_date: date | None = None) -> float:
    if not live_patch_active(on_date):
        return 0.0
    return _PATCH_ROLE_SCORE.get((role, champion), 0.0)


def live_patch_draft_adjustment(champions: dict[str, str], *, on_date: date | None = None) -> float:
    if not live_patch_active(on_date):
        return 0.0
    total = sum(_PATCH_ROLE_SCORE.get((role, champ), 0.0) for role, champ in champions.items())
    return max(-1.6, min(1.6, total))
