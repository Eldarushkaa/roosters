# Isolated Arena visual preview

Run from the repository root:

```sh
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory web
```

Open **http://127.0.0.1:8765/arena-preview/** (Russian), or append `?lang=en`.
This is the project's existing zero-build HTML/CSS/ES-module stack.
The production application does not import this page or its stylesheet.

## Isolation and interactions

- `fixture.mjs` is an explicit art-review fixture. Quotes and results are demonstration data, not a live economy implementation.
- The page imports the shared localization, environment and icon helpers. No `app.js`, API client, authentication, production storage, database or polling.
- CSP `connect-src 'none'` prohibits fetch, XHR, WebSocket and beacon traffic. Forms are prohibited as well.
- Modes, stakes, language and rules are real HTML controls. Fight displays a local 1.2-second loading state, then a demonstration notice. It never charges coins or changes a balance/result.
- The four existing destinations remain visible. Arena is active; the other three explain that only Arena is available in this isolated preview. No other screen is redesigned or booted.
- The navigation occupies its own layout row. The content scrolls above it; nothing is positioned behind the navigation. On 360×560 and 320×568, reaching the CTA requires a short scroll. The shorter layout reduces illustration height and spacing, preserving 14px explanations and 44px controls.
- No full-screen CSS scaling and no screenshot-as-UI approach.

## Resources

The two WebP files in `assets/` are byte-identical copies of:

- `docs/design/assets/arena-background.webp`
- `docs/design/assets/rooster-hero.webp`

They are copied into this directory so the preview also works under Flask's existing static route (`/static/arena-preview/index.html`) without a backend route change. There is no fallback character/background.

Nunito variable font (including Cyrillic) is served locally, weight 1000 for display text and system sans-serif for body text. Source: [Google Fonts](https://github.com/google/fonts/tree/main/ofl/nunito). The font and its OFL license are shared from `../assets/fonts/`.

`../icons.svg` is the shared application sprite, accessed through `../icons.mjs`. The result portrait reuses the robot symbol; a detailed painted opponent portrait is not supplied. Buttons, panels, progress, selectors and navigation consume `../foundation.css`; `preview.css` owns only Arena composition and local sizing. See [Wave 1 foundation](../../docs/design/FOUNDATION.md).

## Reproducible states

Append `?state=loading`, `unavailable`, `noquote`, `error`, `empty`, `victory`, `first`, or `long`. Combine with `&lang=en` as needed. `first` shows the existing free-first-battle concept with demonstration values. The default scene is a returning player with a last defeat, following the reference.

## Browser verification

```sh
.venv/bin/python -m scripts.check_arena_preview
```

The checker captures actual Chrome renders at 390×844, 360×640, 360×560, 320×568 and 430×932, RU/EN and light/dark. Images are exported at CSS viewport dimensions from a 2× device context. It also checks state variants, unchanged balance/results after Fight, selection preservation, minimum target height, navigation separation, loaded art/font and the network prohibition. Native safe-area/theme events use a simulated Telegram bridge; this does not verify a physical Telegram WebView or native chrome.

Screenshots and the JSON report are under `artifacts/arena-preview/`. `*-fight.png` shows the CTA after scrolling it into view; `*-support.png` shows rules/history lower on the same page. The unsuffixed viewport image starts at the top.

## Comparison with the approved reference

The composition uses the blue illustrated arena, a foreground rooster overlapping the wooden nameplate edge, cobalt stats and risk panels, gold selected controls/CTA, and purple duel/result accents. Labels and values are live DOM with existing RU/EN copy and number formatting.

Accepted preview differences from the reference:

- The generated rooster faces slightly more forward; background architecture and banner positions differ from the reference.
- Nunito is rounder and wider than the reference's condensed display lettering. Modes and secondary text consequently have different proportions.
- CSS wood, bevels and decorations are simpler than the painted reference; no large skull decoration or illustrated robot opponent portrait.
- Readable phone-sized explanations make the page taller than the scaled reference. Supporting sections scroll; the connection is a compact green indicator beside the player name.
- Navigation icons are now shared with the live app. No screen layout work on Gear/Roost/Rankings is included.

The visual direction and critical illustrations are approved. Waves 1–3 and the
final consistency pass have applied the shared system across the live app.
Production Arena now renders the same approved background and hero from
`../assets/arena/`, with live breed, level, power and XP. Its composition belongs
to `renderArenaScene` in `../app.js` and `.arena-scene` in `../arena.css`.
The preview continues to use isolated demonstration data. For current ownership
and verification, see the [design handoff](../../docs/design/README.md).
