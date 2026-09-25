# Final visual polish — 2026-09-25

Scope: presentation after the redesign and consistency review. Followed `DESIGN.md`,
`frontend-design` and `telegram-miniapp-design`. The existing screen order, actions,
server commands, prices, rewards, rules, localization meaning and Telegram shell
remain intact. No backend files, environment module, framework or dependency changed.

## Audit before editing

The merged app was run against a disposable database. The existing cross-screen
browser matrix passed all 20 viewport/theme/language combinations before changes.
Screenshots and the visible icon, animation and haptic call sites were reviewed.
The proposed changes were presented in chat before implementation:

| Area | Finding | Applied polish |
| --- | --- | --- |
| Iconography | Chess pieces, Unicode currency/power/arrows and unrelated inline control icons | One small SVG sprite with consistent geometry and stroke weight |
| Motion | Inconsistent pressed feedback; decorative rooster/radar pulses; no confirmed-value accent | Immediate small press, short event accents, remove decorative pulses |
| Haptics | Every rapid battle tap requested vibration | Single event haptics for battle start, confirmed upgrades/equip/rewards, outcome and command failure |
| Micro-visual | Onboarding stars, glass power badge, decorative containers and tiny metadata | Preserve rooster artwork; quieter surfaces, clearer numerals and selected indicators |

## Implemented

**Icons.** Sixteen 24×24 symbols: crossed swords, equipment helmet, roost, trophy,
power, coin, check, close, outward/right/down arrows, feather, rooster head, refresh,
alert and search. Normal strokes are 2.2px with rounded ends/joins; the selected
navigation icon is slightly heavier. No remote icon font or package is loaded.
The rooster head replaces the generic letter/diamond brand mark. Existing detailed
rooster and equipment illustrations remain illustrations, rather than control icons.

Currency glyphs and functional symbols in trusted translation templates become SVG.
Parameters are escaped separately: player names cannot inject markup or have their
symbols interpreted as icons. Coin icons have localized accessible labels where a
unit is needed; redundant icons are hidden from assistive technology.

**Interaction and motion.** Pressed controls scale to .985 immediately; release uses
a short transition. New event accents last 180ms and animate only opacity/transform.
Battle start briefly emphasizes the control and phase label. The result heading
fades in while the notice surface stays opaque. Existing bounded fighter/tap effects
remain; no screen shake or extra particles were added. Decorative loading-rooster
and queue-radar pulses were removed; the existing loading bar remains a progress cue.

The wallet briefly uses success/danger color and opacity only after a changed,
confirmed balance for the same player. Initial state, unchanged polls and account
changes do not animate the balance. The displayed final value remains the existing
server value and formatter; no number tween or predicted balance is introduced.

**Haptics.** Medium impact for an observed preparation→active transition and confirmed
upgrade, breed selection, daily/passive reward. Success notification for a live
victory, warning for defeat, error for a failed command. Event IDs/command keys
deduplicate repeated responses. Historical results, routine polling, navigation,
selectors and rapid taps are silent. Missing/old/throwing bridges, inactive apps and
browser fallback do not depend on haptics. Telegram's documented HapticFeedback
version boundary is 6.1: <https://core.telegram.org/bots/webapps#hapticfeedback>.

**Visual details.** Navigation keeps its geometry and safe areas, with heavier
selected labels/icons. Language selection is underlined; stake and mode selection
have an explicit check. The first-visit order and primary action stay in place.
Removed its decorative stars, gradient surfaces, shadow filter and glass badge
treatment; retained the rooster composition. Money, equipment effects and battle
numbers use tabular numerals. Metadata tracking and supporting type are more uniform.

## Accessibility and performance

- Existing 44px targets and 48px primary actions remain. Matrix assertions cover
  navigation labels, target visibility, overflow and accessible control names.
- Keyboard focus has a visible 3px outline. Selection has shape/text/weight cues as
  well as color. Centralized theme contrast handling is unchanged and unit-tested.
- `prefers-reduced-motion` disables new feedback animations and pressed transforms;
  confirmed values and semantic text remain available. Existing reduced-motion
  combat behavior is retained.
- Sprite plus two small modules total about 6.3KB uncompressed. Same-origin SVG reuse
  avoids a UI dependency. No new polling loop, animation loop, animated shadow or blur.
- Repeated pulses cancel their previous animation. Haptic deduplication is bounded.
  The battle button's rich label is replaced only when its state/language changes,
  rather than inserting SVG markup every timer tick.
- Performance was assessed through implementation and browser behavior, not a native
  frame-time benchmark. Device profiling remains necessary for low-end WebViews.

## Verification matrix

Sizes: **320×568, 360×560, 360×640, 390×844, 430×932**.
All five sizes were exercised in **light/dark × RU/EN**. Existing shell tests also
exercise ordinary browser fallback in both themes, live theme changes, stable and
unstable viewport events, native device/content insets and activation/recovery.
The simulated insets include 24px device + 16px content at the top and 20px + 10px
at the bottom, with asymmetric horizontal insets.

| Coverage | States |
| --- | --- |
| Arena | First visit, returning player, bot/online selection, stake, insufficient funds, queue/recovery |
| Battle | Preparation, active, short-screen controls, rapid input, retry, cap, victory, defeat, navigation restore |
| Gear | Affordable, insufficient funds, capped, owned/equipped, actual upgrade/equip, loading, lost response, error |
| Roost | Ready/unavailable/claimed, actual rewards, retries/errors, large values, Telegram share and clipboard fixtures |
| Rankings | Power/PvP, loading, populated, empty, errors, refresh and stale-response behavior |
| Shared | Boot loading/error/retry, command error, labels/focus, themes, safe areas, scrolling and browser fallback |

Screenshots were reviewed at individual size and as cross-screen contact sheets.
The new feedback matrix explicitly checks both outcomes and actual upgrade/equip/
reward actions in every combination, plus pressed geometry, keyboard focus,
optional haptic failure and reduced motion.

### Commands

```sh
.venv/bin/python -m pytest -q
node --check web/app.js
node --test web/tests/*.test.mjs
.venv/bin/python -m scripts.check_browser
.venv/bin/python -m scripts.check_arena_browser
.venv/bin/python -m scripts.check_battle_browser
.venv/bin/python -m scripts.check_gear_browser
.venv/bin/python -m scripts.check_roost_browser
.venv/bin/python -m scripts.check_rankings_browser
.venv/bin/python -m scripts.check_shell_browser
.venv/bin/python -m scripts.check_consistency_browser
.venv/bin/python -m scripts.check_polish_browser
```

Tests use real local Flask endpoints and temporary SQLite databases; Telegram is
simulated. No real account, production database or bot message was used.

Final results: **173 Python tests passed; 49 Node tests passed; all eight existing
browser suites passed; the new 20-combination visual-polish suite passed.**
`node --check web/app.js` also passed. The new suite saved 140 screenshots; 16 contact
sheets cover the cross-screen comparisons and both outcomes. Evidence is in
[`artifacts/visual-polish/`](../artifacts/visual-polish/).

The regression harness also received narrow corrections: atomic DOM measurements
for shell/Gear visibility avoid reading detached rows during a state refresh;
wallet assertions compare localized displays rather than misreading EN grouping
commas as decimal separators. Existing assertions and behavioral coverage remain.

## Files changed in this polish pass

- `web/icons.svg`, `web/icons.mjs`: sprite and safe rendering helper.
- `web/feedback.mjs`: optional cosmetic motion/haptic feedback.
- `web/app.js`, `web/index.html`: icon markup and feedback event hooks.
- `web/styles.css`: icon, pressed, selection, type and surface polish.
- `web/tests/client.test.mjs`: real icon helpers in the client harness; money assertions
  retain their exact amounts with SVG markup.
- `web/tests/feedback.test.mjs`: six tests for safe rendering, event deduplication,
  bridge failure, confirmed balance changes and reduced motion.
- `scripts/check_polish_browser.py`: 20-combination feedback matrix.
- `scripts/check_browser.py`, `scripts/check_shell_browser.py`,
  `scripts/check_gear_browser.py`: test-harness corrections described above.
- This report and visual evidence under `artifacts/visual-polish/`.

## Deliberately retained / native follow-up

No entrance choreography, extra badges, coin particles, sound, spring motion or
illustration expansion was added. Existing inline confirmations, recoverable
command notices and battle-result notice keep their established roles.

On the smallest viewport with enlarged native insets, onboarding and some secondary
battle details retain natural scrolling. Reordering/compressing that information is
outside this polish pass. The primary battle control remains reachable and clear
of native chrome; normal-screen actions can be scrolled clear of navigation.

Still requires real **Telegram iOS and Android**: perceived haptic strength and
notification mapping, OS haptic settings, low-end frame pacing, VoiceOver/TalkBack,
WebView SVG rendering/caching, native keyboard/orientation/viewport gestures and
share-sheet/clipboard behavior. Browser simulation cannot certify those behaviors.
