"""Local-only custom/bot Draft Coach test controls.

Examples:
  python tools/intent_draft_test.py ban Camille
  python tools/intent_draft_test.py enemy Top Darius
  python tools/intent_draft_test.py ours Jungle Amumu
  python tools/intent_draft_test.py clear

The bridge ignores these overrides outside custom / versus-AI testing.
"""
from __future__ import annotations

import argparse
import json
import urllib.request

URL = "http://127.0.0.1:8766/api/test"
ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]


def post(payload: dict) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ban"); p.add_argument("champion")
    p = sub.add_parser("unban"); p.add_argument("champion")
    p = sub.add_parser("enemy"); p.add_argument("role", choices=ROLES); p.add_argument("champion")
    p = sub.add_parser("ours"); p.add_argument("role", choices=ROLES); p.add_argument("champion")
    sub.add_parser("clear")
    args = parser.parse_args()
    if args.cmd == "ban": payload = {"ban": args.champion}
    elif args.cmd == "unban": payload = {"unban": args.champion}
    elif args.cmd == "enemy": payload = {"enemy_pick": {"role": args.role, "champion": args.champion}}
    elif args.cmd == "ours": payload = {"our_pick": {"role": args.role, "champion": args.champion}}
    else: payload = {"clear": True}
    print(json.dumps(post(payload), indent=2))


if __name__ == "__main__":
    main()
