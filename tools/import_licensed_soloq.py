"""Import an authorised champion/matchup/pair export into one validated snapshot.

Input JSON contract:
{ "source": {"name": "...", "permission_reference": "contract URL or written approval"},
  "patch": "16.20", "queue": "RANKED_SOLO_5x5",
  "rank": "Emerald+", "region": "Global",
  "champions": [{"role":"Mid","champion":"Veigar","games":5000,"win_rate":51.7}],
  "matchups": [{"role":"Mid","enemy":"Ahri","response":"Veigar",
                "games":1200,"response_advantage":2.4}],
  "pairs": [{"role_a":"ADC","champion_a":"Jinx","role_b":"Support",
             "champion_b":"Lulu","games":1500,"lift_pp":1.1}] }

A source's `permission_reference` must refer to **real authorisation**.
Unknown/invalid/stale inputs never alter the published snapshot.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from league_aggregate_sources import ROLES, validate_snapshot

def clean(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("input must be JSON object")
    source = data.get("source")
    if not isinstance(source, dict) or not str(source.get("name") or "").strip():
        raise ValueError("source name required")
    ref = str(source.get("permission_reference") or "").strip()
    if len(ref) < 12:
        raise ValueError("real licence or written permission reference required")
    patch = str(data.get("patch") or "")
    if len(patch.split(".")) != 2 or not all(x.isdigit() for x in patch.split(".")):
        raise ValueError("exact numeric patch required")
    if data.get("queue") != "RANKED_SOLO_5x5":
        raise ValueError("only Ranked Solo/Duo evidence supported")
    if not data.get("rank") or not data.get("region"):
        raise ValueError("rank and region required")
    result = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "patch": patch,
        "queue": "RANKED_SOLO_5x5",
        "sources": [{"name": str(source["name"]).strip(),
                     "permission_reference": ref,
                     "rank": str(data["rank"]), "region": str(data["region"]),
                     "source_captured_at": str(source.get("captured_at") or ""),
                     "public_redistribution_allowed": source.get("public_redistribution_allowed") is True}],
        "champions": [], "matchups": [], "pairs": [],
    }
    for row in data.get("champions", []):
        try:
            role = str(row["role"])
            champ = str(row["champion"]).strip()
            n = int(row["games"])
            wr = float(row["win_rate"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("invalid champion row") from exc
        if role not in ROLES or not champ or n < 300 or not math.isfinite(wr) or not 0 <= wr <= 100:
            continue
        result["champions"].append({
            "role": role, "champion": champ, "games": n,
            "win_rate": round(wr, 3), "source": source["name"],
        })
    for row in data.get("matchups", []):
        try:
            role = str(row["role"])
            enemy = str(row["enemy"]).strip()
            own = str(row["response"]).strip()
            n = int(row["games"])
            advantage = float(row["response_advantage"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("invalid matchup row") from exc
        if role not in ROLES or not enemy or not own or enemy == own or n < 120 or not math.isfinite(advantage):
            continue
        if abs(advantage) > 30:
            continue
        result["matchups"].append({
            "role": role, "enemy": enemy, "response": own,
            "games": n, "response_advantage": round(advantage, 3),
            "source": source["name"],
        })
    for row in data.get("pairs", []):
        try:
            a, ac = str(row["role_a"]), str(row["champion_a"]).strip()
            b, bc = str(row["role_b"]), str(row["champion_b"]).strip()
            n, lift = int(row["games"]), float(row["lift_pp"])
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("invalid pair row") from exc
        if a not in ROLES or b not in ROLES or a == b or not ac or not bc or n < 150 or not math.isfinite(lift) or abs(lift) > 30:
            continue
        result["pairs"].append({
            "role_a": a, "champion_a": ac, "role_b": b, "champion_b": bc,
            "games": n, "lift_pp": round(lift, 3),
            "source": source["name"],
        })
    if not any(result[key] for key in ("champions", "matchups", "pairs")):
        raise ValueError("no usable observations; refusing empty override")
    if not validate_snapshot(result):
        raise ValueError("normalised snapshot did not pass schema validation")
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="approved provider JSON export")
    parser.add_argument("--output", default=str(ROOT / "data" / "league_licensed_soloq.json"))
    args = parser.parse_args()
    source = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    data = clean(source)
    destination = Path(args.output)
    public_snapshot = (ROOT / "data" / "league_licensed_soloq.json").resolve()
    if destination.resolve() == public_snapshot and not data["sources"][0]["public_redistribution_allowed"]:
        raise ValueError(
            "Refusing publication in PUBLIC GitHub repo: explicit permission "
            "to redistribute aggregate data is required. Use --output to "
            "write a private local snapshot instead."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".new")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    print(json.dumps({
        "patch": data["patch"], "source": data["sources"][0]["name"],
        "champions": len(data["champions"]),
        "matchups": len(data["matchups"]), "pairs": len(data["pairs"]),
        "output": str(destination),
    }))
if __name__ == "__main__":
    main()
