# Mini App frontend

The Flask application serves `index.html` at `/` and these assets under `/static/`.
There is no build step, external font, package install, or frontend secret. The
only external script is Telegram's official Mini App bridge.

`environment.mjs` owns the Telegram/browser shell (theme, stable viewport, safe
areas, native chrome and lifecycle); screen renderers never subscribe to native
layout events. `app.js` is a browser ES module with four areas: authenticated API transport and
write recovery; authoritative state/rendering; screen renderers; UI/timer event
handlers. It imports the dependency-free `i18n.mjs` translation catalog.
`styles.css` provides responsive layouts from 320 px, visible keyboard
focus, safe-area spacing, and reduced-motion support. The rooster, equipment,
and favicon are original inline SVG artwork.

## Languages

The header's RU/EN buttons switch the complete interface, including loading and
sign-in errors, all four screens, battle/reveal/result text, help, notices,
accessibility labels, equipment and breed descriptions. Russian is the default.
The preference lives in `localStorage` under `rooster.v1.language`; denied storage
still permits switching for the current document. Number, coin and date formats
follow the selected locale (`ru-RU` or `en-US`), with dates in the device timezone.
Changing language sends no HTTP request and does not alter the server's player,
balance, commands, battle, or accepted and buffered taps.

`i18n.mjs` uses semantic keys and explicit `{parameter}` interpolation. Values are
plain text; `app.js` escapes them when composing HTML. Static nodes declare
`data-i18n`, `data-i18n-aria`, `data-i18n-title` or `data-i18n-content`; no global
search/replace touches rendered text. Catalog names use stable breed/slot IDs.
Actual player names stay exactly as returned by the server. Only known names on
fighters explicitly marked `is_bot` may be translated.

Notices retain a key and parameters so an already visible message changes
language too. API errors are translated by stable error codes; unknown codes use
a safe generic message. Retried commands retain their original UUID and body,
while success text is translated at display time. Legacy pending commands with
Russian success prose map to their operation's known message key. During combat,
only preserved fighters' text labels and SVG accessibility labels are refreshed;
motion wrappers, animations and Space input remain intact.

## State and recovery

- Active battles use a focused viewport layout from preparation until the server
  returns a finished battle. Global navigation, the wallet and other shell chrome
  are suppressed; language selection remains available. The tap controls occupy
  a reserved bottom grid row, while fight details, help and the previous result
  scroll above it. The shared environment tokens bound the layout. All screens
  support live light/dark themes without replacing fighters or resetting scroll.
- Telegram `initData` is sent unchanged to the backend for validation. No trust
  is placed in `initDataUnsafe`. Browser accounts exist only when `/config`
  enables development auth. Their random identity is kept in local storage.
- Bearer sessions are held in session storage and renewed once on a 401. A
  matching Telegram account can reuse a still-valid signed session even when
  its original launch payload has expired. A different launch account or a
  dev/Telegram switch always authenticates again; an unsigned launch ID only
  selects the cache and never authorizes access.
  Automatic renewal only updates credentials: its potentially older state must
  not overwrite a concurrent completed purchase. The original caller controls
  state ordering and polling generation checks.
- Every mutation is serialized and written to session storage with its original
  UUID, body, route, and account ID before sending. A network error, timeout,
  malformed successful response, or server error retains that exact command.
  The retry button and reconnect/reload recovery reuse it. Another account
  cannot replay a saved command belonging to the previous account.
- Definite business errors clear the pending command and refresh authoritative
  state. In particular, cancellation racing with a PvP match reveals the active
  battle instead of pretending the player has left it.
- Tap input is aggregated every 400 ms, up to all 90 useful taps in one request.
  There is no client pace limiter. Only
  the accepted totals returned by the server are displayed. No optimistic
  rewards, balances, win probabilities, or online counts are fabricated.
- Each combat click immediately plays a local lunge, opponent recoil, sword
  swipe and sparks. This is input feedback, not a confirmed hit or damage value.
  At most one set of Web Animations runs: rapid clicks restart it without adding
  DOM nodes. Fighter nodes survive polling and pending-command renders, and the
  effect is cancelled when leaving combat. Reduced motion uses a subtle opacity
  accent only. Browsers without Web Animations can still send taps normally.
- Space on the arena sends the same combat input and animation as a click.
  One physical press counts once; holding Space does not auto-repeat. Consumed
  keydown/keyup events prevent scrolling and a second native button click.
  Text fields and other focused controls retain their normal keyboard actions;
  preparation, the deadline and the server tap cap still govern accepted input.
- Battles use the server's 10-second duration and 90-tap total limit. The UI
  reads timing and limits from the catalog/battle state. Preparation has its own
  countdown with taps disabled; the local timer enables combat at `starts_at`
  without waiting for another poll. Existing legacy battles keep their original
  timeline and tap limit, and display their old reward rules honestly.
- There is one balance, represented by integer minor units (100 = one coin).
  Coin formatting uses at most two decimal places. Stakes and payouts remain
  server-authoritative. New players see the one-time free introduction before
  the hero; after it, the bot mode offers stakes of 10/25/50/100 coins. Online
  matches require equal stakes and award 95% of the combined pool to the winner.
- Bot starts send `mode`, `stake_minor`, and `expected_power`; matchmaking sends
  `stake_minor`. Before committing, the bot card discloses the server's payout
  range and possible loss. The selected opponent and exact prize are fixed once
  by the server. The two-second reveal is cosmetic, lands on the saved power,
  resumes from the saved deadline on reload, and honors reduced motion. Exact
  prize stays visible throughout preparation. There is no reroll control.
- The result shows both gross payout and net change. It never automatically
  buys or suggests an upgrade. Gear remains a separate user-selected action.
  A compact, non-dismissible Last Battle card uses the latest completed server
  battle/history, survives reload, and stays below the combat or queue view
  until the next result replaces it. Old local dismissal flags are ignored.
  A separate victory/defeat notice closes with its cross or four seconds after
  first display. The ring around the cross drains against the same deadline;
  polling, changing tabs and switching language never restart the timer.
  `sessionStorage` remembers the last observed result for each player, preventing
  a repeat notice after reload in the same tab. With storage denied, this
  protection lasts only for the current document. Results first observed while
  another battle or search is active are marked as seen without a popup.
  Closing the notice never changes a reward or removes the persistent card.
- Breed multipliers apply to the whole sum of base, level and equipment power;
  the server rounds the final value down once. The active battle displays
  `current_win_probability` after preparation, using server-accepted taps.
  An immediate hit animation does not optimistically change the displayed odds.
  Opponent taps can lower the current chance in PvP. Historical v1/v2 battles
  return `null` for this field and do not display the live odds panel.
- Leaderboards have server-sorted power and real-PvP-win lists. Development
  matches never add real PvP wins. Out-of-battle tapping, energy, a second
  currency, and the separate practice mode are absent.
- Idle state polling runs every 10 seconds, active battle/queue polling every
  2 seconds. Presence runs every 15 seconds while visible. Inactive documents
  do not send heartbeat or polling traffic. Returning to the app refreshes
  state. Battle countdowns use the latest server clock offset and never settle
  a result locally.
- Real and development counts and leaderboards are explicitly separated.
- Server text is HTML-escaped, and breed colors are restricted to hex values.

## Check

```sh
node --check web/app.js
node --check web/i18n.mjs
node --test web/tests/*.test.mjs
```

`python -m scripts.check_battle_browser` runs the focused Battle browser review
against a temporary database. It covers 360/390/430px, a 360×560 short viewport,
320px, RU/EN, light/dark, simulated Telegram safe areas and viewport changes,
scroll preservation, reload from another tab, lost tap response/retry, the tap
cap, and restored navigation after settlement. Screenshots go to
`artifacts/battle-layout`. Native Telegram gestures still require device review.

The dependency-free Node regressions cover response loss, server errors,
definite business conflicts, authentication refresh failures, session reuse,
development/Telegram identity separation, and the simplified battle contract.
They also verify the preparation/combat boundary independently of state polling,
an 80-tap server limit in one batch, minor-unit precision, first free action, legacy reward
text, and a late authentication response racing with a completed purchase.
Localization checks cover RU default and EN persistence, switching before auth
or during combat, immutable pending commands and tap buffers, translated catalog
IDs, escaped unchanged player names, notices after switching, complete catalog
keys/placeholders and server error coverage, and locale-specific money/dates.
They use a minimal DOM shim; actual browser behavior is checked separately.
The v4 Node additions check breed multiplier text, rendering a server-supplied
chance with a 90-tap limit, and notice dismissal/deadline/reload behavior. The
older batch regression still uses an 80-tap fixture; the current 90-tap flow
is exercised through the real API in Chrome.

`python -m scripts.check_browser` uses a temporary database and checks RU/EN in
Chrome, including all four English screens, reload persistence, a 320 px header,
the compact Last Battle card, and switching during a paused combat animation
without replacing fighter nodes or sending extra taps. The v4 scenarios also
send all 90 taps in one request, check increasing server odds, purchase a breed
that multiplies 110 power to 132, and verify the four-second notice, draining
ring, manual close and protection against repeated display. Install optional browser
dependencies from `requirements-browser.txt` as described in the root README.

Manual/browser checks should cover: 320 px viewport with no horizontal scroll;
keyboard navigation; first-free loss reward; paid bot wins/losses; gear purchase;
all 90 taps accepted in one batch; equal/different-stake matchmaking; refreshing
during the opponent reveal; disconnect after a paid start or purchase and
retrying the same UUID; failed
queue cancellation after a match; HTML-like player names; and Telegram auth with
development auth disabled. Online counters require visible heartbeat senders,
so a hidden browser profile correctly expires from presence.

For referral sharing, configure the bot's Main Mini App as described in
`docs/TELEGRAM_SETUP.md`; setting a chat menu alone is not sufficient for
`?startapp=` links.

## Telegram runtime and browser fallback

The shell initializes before authentication, then calls `ready()` once after the
localized loading screen is available and `expand()` once. Boot retries and
resume do not repeatedly expand the app. No fullscreen request is made.

`environment.mjs` treats an SDK with an unknown platform and empty launch data as
a normal browser. Telegram's `colorScheme` and `themeParams` supply semantic
`--bg`, `--panel`, `--panel-light`, `--text`, `--muted`, and `--line` tokens.
Incomplete or inverted surfaces fall back to the warm Roosters palette; primary,
muted and semantic status text are checked against all three surfaces at 4.5:1.
Amber remains the action fill; `--accent` is its readable text counterpart.
Browser fallback follows `prefers-color-scheme`, including live changes.
`themeChanged` only updates root tokens and surrounding native chrome.

Native calls are feature-detected, version-gated and guarded against bridge
exceptions. Header/background setters require 6.1; headers use `bg_color` on
6.1–6.8 and a custom hex color from 6.9. Bottom bar coloring requires 7.10.
Safe/content-safe events and activation events require 8.0. Older clients keep
CSS/browser fallbacks. The document's `theme-color` tracks the surface too.
API version boundaries: https://core.telegram.org/bots/webapps

`--app-height` uses `viewportStableHeight`, with Telegram's stable CSS variable
as a fallback, and browser `100dvh` outside Telegram. Unstable `viewportChanged`
events are ignored; browser resize does not override Telegram's stable height.
Short-screen variants follow the settled height minus native insets, rather than transient media
query changes. Bottom navigation is positioned from this stable height; Battle
keeps its existing reserved control row and scrollable overview. During a native
resize gesture the app waits for the settled event instead of chasing its edge.

Each device inset uses Telegram's numeric value (including zero), then its CSS
variable, then `env(safe-area-inset-*)`. The browser inset is a fallback, never an
additional padding. The separate content-safe inset is added once. All four
edges are shared by page content, Battle, navigation and floating notices. A
fixed background border masks scrolling content beneath transparent native
chrome without adding padding.
There are no bottom sheets in the current UI. New sheets should use these same
`--app-inset-*` and `--app-height` tokens.

On `activated`, visible-document recovery, or restored browser pages, the shell
resyncs its environment and invokes the existing authoritative state refresh.
Theme and viewport events never mutate gameplay, reauthenticate, or render a
screen. Browser visibility recovery remains available on older clients.

Validation:

- `node --test web/tests/*.test.mjs`: existing transport/localization tests plus
  runtime compatibility, contrast, fallback, inset and lifecycle regressions.
- `python -m scripts.check_shell_browser`: disposable real backend plus simulated
  Telegram bridge; 360×560, 360×640, 390×844, 430×932, and 320×568 in browser and
  Telegram light/dark (20 combinations). Checks returning Arena, Battle, results,
  navigation, live themes, safe/content-safe events, actual WebView resize during
  unstable events, settled collapse/expand, DOM/scroll preservation and resume.
  Screenshots are written to `artifacts/shell-layout/`.
- Existing `scripts.check_browser` and `scripts.check_battle_browser` remain the
  gameplay and focused Battle regression suites.

Native Telegram gestures, actual notch/gesture-bar inset reporting, keyboard
resizes, chrome appearance and resume behavior still need physical iOS/Android
(and desktop Telegram) QA. A simulated bridge cannot certify native behavior.

## Gear progression screen

Gear uses a compact current-fighter summary, vertical `renderUpgradeRow` items,
then illustrated `renderBreedChoice` entries. Existing rooster/equipment SVGs,
`.btn` / `.btn.primary`, theme tokens and the locale-aware `money()` formatter
are reused. `renderGearAction` shares price, availability and retry presentation
between upgrades and breeds; these helpers stay local to Gear. The old
`equipment-grid` selector remains for existing browser regressions, with no
three-column styling.

All costs, levels, ownership, multipliers and total power come from state/catalog.
The benefit text expresses base equipment power from the catalog; it does not
predict rounded final fighter power. A null next price means maximum level.
Buying an unowned breed also equips it, as before. Equipped breeds have a text
marker and no Equip button; owned breeds have a neutral Equip action.

The existing serialized, persisted command transport is unchanged. Only the
active item shows processing; other money actions explain their wait, while
navigation and scrolling remain available. Gear-local success/errors retain
translation keys. An uncertain request exposes a local retry that delegates to
`sendPending()` with the original UUID/body, alongside existing global recovery.
No speculative balance, level or ownership updates are made.

`python -m scripts.check_gear_browser` uses a temporary real backend and simulated
Telegram insets. It checks 320×568, 360×560, 360×640, 390×844 and 430×932 in both
languages and both themes (20 combinations), affordable/insufficient/max/owned/
equipped states, touch targets, readable text, scroll clearance and exact money.
It also verifies actual upgrade/buy/equip payloads, local processing, response
loss after commit with an identical retry and one charge, definite errors and
matchmaking locks. Screenshots are in `artifacts/gear-layout/`. Native Telegram
WebView gestures and real device inset reporting still require device QA.
