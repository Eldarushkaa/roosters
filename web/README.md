# Mini App frontend

For new UI work, start with the [current design handoff](../docs/design/README.md)
and [visual specification](../DESIGN.md). The handoff maps screen/shared ownership,
test commands and the distinction between live Arena and the isolated preview.

The Flask application serves `index.html` at `/` and these assets under `/static/`.
There is no build step, external font, package install, or frontend secret. The
only external script is Telegram's official Mini App bridge.

`environment.mjs` owns the Telegram/browser shell (theme, stable viewport, safe
areas, native chrome and lifecycle); screen renderers never subscribe to native
layout events. `app.js` is a browser ES module with four areas: authenticated API transport and
write recovery; authoritative state/rendering; screen renderers; UI/timer event
handlers. It imports the dependency-free `i18n.mjs` translation catalog.
`styles.css` provides responsive layouts from 320 px, visible keyboard
focus, safe-area spacing, and reduced-motion support. Arena uses the approved
WebP background and hero; Battle, Gear and Roost keep their existing inline SVG
artwork. The favicon is a standalone export of the shared rooster icon.

## Shared visual foundation and final consistency pass

`foundation.css` provides opt-in illustrated `ui-*` components and `--ui-*`
tokens. It is loaded after the legacy styles. The approved Arena preview and
production bottom navigation use the same primitives, local display font and
`icons.svg` sprite. Gear, Roost and Rankings now reuse those components with
layout-only `gear.css`, `roost.css` and `rankings.css`. Battle preparation and play
use `battle.css`; the result notice uses
`battle-result.css`. The actual stylesheet order is `styles`, `gear`, `roost`,
`rankings`, `foundation`, `shell`, `arena`, `battle`, `battle-result`, `guide`; do not assume
every composition stylesheet follows the foundation. Battle
retains its half-viewport tap area and scrolling overview; its title/timer share
the header row with language controls. `arena.css` now applies shared components
to the existing production Arena, queue and history layout; `shell.css` aligns
the header, wallet, loading/error states and notices. The `ui-shell` environment
stays active on every screen and before authentication. Telegram's environment
module retains runtime ownership. Production Arena renders the approved background
and hero from `assets/arena/` through `renderArenaScene(player, onboarding)` and
`.arena-scene`: a wooden breed nameplate and live level, power and XP accompany the
illustration. On first launch, the scene follows the existing free-battle CTA.
The preview remains an isolated fixture; its demonstration economy is not imported.
See [the final review](../docs/design/FINAL-POLISH.md),
[Wave 3](../docs/design/WAVE3.md),
[Wave 2](../docs/design/WAVE2.md) and
[the component/token catalog and historical Wave 1 boundary](../docs/design/FOUNDATION.md).

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

The current `bot` mode is labeled `Тренировка` / `Training` throughout the UI.
Its API mode, v6 rules, stake and payouts are unchanged; paid training can lose
the stake. Archived `practice` battles retain their separate legacy meaning.

## Quick guide

`guide.mjs` owns the informational modal through
`createGuide({overlay, dialog, trigger, background, storage, getContent,
getLanguage, fallbackFocus, doc})`. `sync({available, autoAllowed})` controls
availability and deferred automatic opening; `open()`, `close()` and `isOpen()`
support the header control and app lifecycle. The guide sends no game command.
`guide.css` owns its layout; `renderGuideContent` and `syncGuide` in `app.js` own
localized content and eligibility. `index.html` owns the header trigger and
overlay hosts. Game actions stay blocked while the guide is open.

On the first browser launch, automatic opening waits for authentication and
pending-command recovery. Active battles, matchmaking, unfinished commands and
the result toast defer it. Manual opening is allowed during matchmaking, but
the trigger is unavailable during combat. A newly active battle closes an open
guide without marking it seen. Explicit closing records `1` under
`rooster.v1.guideSeen.v1` in localStorage, so it stays dismissed across reloads
in that browser; unavailable storage falls back to this document's memory.

The modal makes the app background inert, traps Tab focus, supports Escape and
explicit close controls, and restores focus on close. Its text region accepts
keyboard focus so PageDown can scroll it on short screens while the cross and
footer remain available. Language changes refresh
its copy while preserving focus and reading position; state polls preserve the
open modal DOM. Its short steps cover training/coins, server-provided tap and
duration limits, gear/breed power, and online progression; Roost rewards remain
an informational footer. The guide does not promise a win or invent rewards.

## State and recovery

- Active battles use a focused viewport layout from preparation until the server
  returns a finished battle. Global navigation, the wallet and other shell chrome
  are suppressed; language selection remains available. The tap controls occupy
  a reserved bottom grid row. The full-width tap button takes half the viewport
  height after Telegram safe-area insets; fight details scroll above it on short
  screens. Closing-game help and the previous result are omitted during battle.
  The shared environment tokens bound the layout. All screens
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
  the hero. Both paid modes offer a slider from 10 coins to the available balance
  in 0.01-coin steps, with 10/25/50/100 quick choices. Online
  matches accept different stakes and powers within the same account scope
  (Telegram or development). Each player's win payout is `floor(1.9 × own stake)`
  in minor units, including the stake. The UI reads it from
  `economy.online_quotes` (`stake_minor`, `win_payout_minor`) instead of calculating
  it from a shared pool. `catalog.battle.pvp_win_multiplier` replaces the old
  `pvp_pool_return`. Power and accepted taps change the win probability and
  expected return; the opponent's stake does not change the personal payout.
  Current bot RTP rises from 90% without taps to 110% at 90 accepted taps before
  payout rounding; the catalog owns these limits. At equal power this means a
  win chance from 50% to 61.11%. Already-started v5/v4/v3 battles retain their rules.
- `economy.stake_limits` supplies `min_minor`, `max_minor`, and `step_minor`.
  The server caps `max_minor` by available balance and arithmetic/storage limits;
  the client does not independently extend that maximum.
  The native `#arena-stake-range` input uses integer minor units, with
  `data-action="stake-range"` and `data-focus="stake-range"`; its caption is
  `#arena-stake-value`. Input updates the selected amount immediately without a
  mutation. Quick choices synchronize the slider; choices above the available
  balance are disabled. The upper end includes every available minor unit, so
  a fractional balance can be selected in full. Below the minimum, paid starts
  and stake controls are disabled. A falling balance clamps the selection;
  polling and language changes retain the range node and the valid selection.
  `renderKeepingStakeRange` keeps the native input and its ancestor path attached
  while updating sibling content, so a poll does not interrupt an active drag.
  `stakeSelectionBlocked` allows local selection during a pending `/presence`
  heartbeat. Paid commands remain serialized and blocked until it completes;
  custom quote loading resumes afterwards. Other pending commands still block
  selection, and no selection rewrites a pending request.
- Preset quotes remain in state. Arbitrary stakes use authenticated
  `GET /battle/quote?stake_minor=N`, returning `rules_version`, `power`,
  `stake_minor`, `bot: {min_payout_minor, max_payout_minor}` and
  `online: {win_payout_minor}`. Requests are debounced, and obsolete responses
  cannot replace the current stake/power quote. Paid starts wait for a current
  server quote. An error exposes `retry-stake-quote`; retrying only reads a quote.
  Neither quote lookup nor a balance-driven selection adjustment rewrites the
  body or UUID of a pending battle command.
- Bot starts send `mode`, `stake_minor`, and `expected_power`; matchmaking sends
  `stake_minor`. A question button at the top right of each mode tile opens its
  payout, possible loss and explanation in the shared in-flow
  `#arena-mode-help` panel inside `.arena-setup`. These details are hidden by
  default. The question and mode controls are sibling buttons: `mode-help`
  carries `data-mode`, `data-focus="help-bot|help-online"`, `aria-expanded` and
  `aria-controls="arena-mode-help"`; it does not select a mode, change a stake,
  or send a request. Repeating the question, closing the panel, pressing Escape,
  or switching mode closes help. A close returns focus to the question. Polling
  and language changes preserve open help, and a stake change updates its server
  quote. The first-free screen offers the same help for its online alternative.
  The selected bot opponent and exact prize are fixed once
  by the server. The two-second reveal is cosmetic, lands on the saved power,
  resumes from the saved deadline on reload, and honors reduced motion. Exact
  prize stays visible throughout preparation. There is no reroll control.
- The result shows both gross payout and net change. It never automatically
  buys or suggests an upgrade. Gear remains a separate user-selected action.
  Arena and the queue no longer render a persistent Last Battle card. Completed
  results remain in server history. The victory/defeat notice closes with its
  cross or four seconds after first display. The ring around the cross drains
  against the same deadline;
  polling, changing tabs and switching language never restart the timer.
  `sessionStorage` remembers the last observed result for each player, preventing
  a repeat notice after reload in the same tab. With storage denied, this
  protection lasts only for the current document. Results first observed while
  another battle or search is active are marked as seen without a popup.
  Closing the notice never changes a reward or removes history.
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

`python -m scripts.check_stake_browser` uses a disposable backend to check custom
quote loading, late responses, failure/retry without mutations, bot commitment,
balance clamping, reload, decimal all-in PvP payouts and the minimum boundary.
Screenshots go to `artifacts/stake-review`. The Arena checker owns the RU/EN,
theme and mobile-size matrix, including actual range dragging and keyboard input
through polling and language changes.

`python -m scripts.check_guide_browser` checks first-launch opening, explicit
dismissal/reload, browser-wide seen state, manual reopening, focus trapping and
restoration, background inertness, scrolling, RU/EN and five mobile sizes in
both themes. It also checks battle/queue deferral without game POSTs. Its
screenshots go to `artifacts/guide-review`; native Telegram behavior still needs
device review. Node guide tests cover denied storage and lifecycle behavior;
client integration tests cover deferred pending-command recovery and unchanged
UUID/body, catalog-driven copy, and blocking game actions while open.

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
The v5 checks cover local mode help without transport, quote/state preservation
across polls, stake and language changes, independent online quotes, and removal
of the persistent result from Arena and queue while retaining history and toast.
The v6 checks cover exact custom stakes, authoritative quote requests and retries,
out-of-order responses, disabled unaffordable choices, balance clamping, and
unchanged pending command UUIDs/bodies. Browser checks cover range-node retention
while polling or changing locale, including mobile pointer/keyboard interaction.

`python -m scripts.check_browser` uses a temporary database and checks RU/EN in
Chrome, including all four English screens, reload persistence, a 320 px header,
absence of the old Last Battle card, and switching during a paused combat
animation without replacing fighter nodes or sending extra taps. The scenarios also
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
a normal browser. Telegram's `colorScheme` and browser `prefers-color-scheme`
still set the native theme identity, including live changes. The semantic game
colors and native header/background/bottom-bar colors are resolved from the
shared `--ui-*` CSS roles in both themes. This prevents native chrome and initial
loading from reverting to the old charcoal/beige palette. If shared CSS is absent,
the existing contrast-checked Telegram palette remains a fallback. `themeChanged`
only updates root tokens and surrounding native chrome; no game state is changed.

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
query changes. Arena uses its dense setup through 700px of usable settled height
to accommodate outlined controls while retaining the action above navigation at
360×560. Battle's existing 650px threshold is unchanged. Bottom navigation is positioned from this stable height; Battle
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
shared `ui-*` panels/buttons/badges, `--ui-*` tokens and the locale-aware `money()`
formatter are reused. `renderGearAction` shares price, availability and retry presentation
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
