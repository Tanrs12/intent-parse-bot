"""Check static JS, unique IDs, patch compatibility and both text palettes.

No app is started. The INTent patch runs only on a temporary packaged-layout copy.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "draftcoach" / "static"


class IDs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.seen = set()

    def handle_starttag(self, tag, attrs):
        value = dict(attrs).get("id")
        if value:
            assert value not in self.seen, f"Duplicate ID: {value}"
            self.seen.add(value)


def check_scripts(root: Path, scratch: Path) -> int:
    count = 0
    for file in root.rglob("*.html"):
        text = file.read_text(encoding="utf-8")
        IDs().feed(text)
        for index, code in enumerate(re.findall(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I)):
            if not code.strip():
                continue
            script = scratch / f"{file.stem}-{index}.js"
            script.write_text(code, encoding="utf-8")
            subprocess.run(["node", "--check", str(script)], check=True)
            count += 1
    for name in ("common.js", "draft-review.js"):
        subprocess.run(["node", "--check", str(root / name)], check=True)
        count += 1
    return count


def contrast(a: str, b: str) -> float:
    def light(hex_color):
        rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    lo, hi = sorted((light(a), light(b)))
    return (hi + .05) / (lo + .05)


def check_palettes():
    css = (STATIC / "mm" / "tokens.css").read_text(encoding="utf-8")
    for name, selector in (("porcelain", ":root"), ("graphite", "body.graphite")):
        block = re.search(re.escape(selector) + r"\s*\{([^}]+)", css).group(1)
        colors = dict(re.findall(r"(--[\w-]+):\s*(#[\da-fA-F]{6})", block))
        ratios = []
        for fg in ("--mm-ink", "--mm-secondary", "--mm-blue", "--mm-warn", "--fd-error"):
            for bg in ("--mm-surface", "--mm-paper", "--fd-raised", "--mm-pale"):
                ratio = contrast(colors[fg], colors[bg])
                assert ratio >= 4.5, (name, fg, bg, ratio)
                ratios.append(ratio)
        action = contrast(colors["--fd-action-ink"], colors["--mm-blue"])
        assert action >= 4.5, (name, "primary button", action)
        print(f"{name}: minimum text contrast {min(ratios):.2f}:1; primary button {action:.2f}:1")


def main():
    # Default mkdir permissions also work under the Windows restricted token.
    root = ROOT / (".ui-check-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        packaged = root / "_internal" / "draftcoach" / "static"
        shutil.copytree(STATIC, packaged)
        print(f"Source: {check_scripts(STATIC, root)} scripts passed node --check; IDs unique.")
        subprocess.run([sys.executable, str(ROOT / "tools" / "patch_flexers_intent_ui.py"), str(root)], check=True)
        print(f"Patched copy: {check_scripts(packaged, root)} scripts passed node --check; IDs unique.")
        # A second invocation must be harmless.
        subprocess.run([sys.executable, str(ROOT / "tools" / "patch_flexers_intent_ui.py"), str(root)], check=True)
    finally:
        assert root.resolve().parent == ROOT.resolve() and not root.is_symlink()
        shutil.rmtree(root)
    check_palettes()


if __name__ == "__main__":
    main()
