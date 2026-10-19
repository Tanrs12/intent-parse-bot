"""Refresh the approved, PUBLICLY REDISTRIBUTABLE aggregate SoloQ export.

This is intentionally dormant until a licensed feed URL has been configured
as a secret in GitHub Actions. It never scrapes a statistics website.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.import_licensed_soloq import clean

def execute(url: str, token: str = "", output: Path | None = None) -> dict:
    output = output or ROOT / "data" / "league_licensed_soloq.json"
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("approved provider URL must be HTTPS with no embedded credentials")
    headers = {"User-Agent": "INTent-Flexers-Approved-Data/1.0",
               "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request,timeout=25) as response:
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ValueError("provider feed too large")
    data = clean(json.loads(raw))
    if not data["sources"][0].get("public_redistribution_allowed"):
        raise ValueError("no PUBLIC redistribution permission; do not commit this provider data to GitHub")
    old = None
    if output.exists():
        try:
            old = json.loads(output.read_text(encoding="utf-8"))
        except ValueError:
            pass
    old_compare = dict(old or {})
    new_compare = dict(data)
    old_compare.pop("generated_at",None)
    new_compare.pop("generated_at",None)
    changed = old_compare != new_compare
    if changed:
        output.parent.mkdir(parents=True,exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".download")
        temporary.write_text(json.dumps(data,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        temporary.replace(output)
    return {"updated": changed, "patch": data["patch"],
            "champions":len(data["champions"]),
            "matchups":len(data["matchups"]),
            "pairs":len(data["pairs"]),
            "source":data["sources"][0]["name"]}

def main():
    url = os.environ.get("SOLOQ_APPROVED_FEED_URL","").strip()
    if not url:
        print("No approved/licensed provider configured; current SoloQ fallback unchanged.")
        return
    result=execute(url,os.environ.get("SOLOQ_APPROVED_FEED_TOKEN",""))
    print(json.dumps(result))
    output=os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output,"a",encoding="utf-8") as f:
            f.write("updated=" + ("true" if result["updated"] else "false") + "\n")
if __name__=="__main__":
    main()
