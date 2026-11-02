# UI dependencies

No additional packages vendored for the 2026-11-02 UI rebuild.

Native `dialog`, `details`, buttons and CSS cover the interactions. The existing
Matchup Master SVG set, system Segoe UI/Consolas fonts and a 180 ms image arrival
transition cover icons, typography and motion. An icon, font or component package
would duplicate these assets without improving the draft decision.

There is no npm build and no CDN dependency in the browser. Champion icons already
in `assets/league_champions` are included by `build_exe.py`. High-resolution art is
served through `/api/art/*`, with successful remote downloads retained in
`var/champion-art`; offline cache misses fall back to the bundled champion icon.
Playwright is used only by the optional Python browser tests, never by the app.
