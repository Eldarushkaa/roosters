"""Exercise the real Mini App in Chrome against a disposable local server.

Run: ``python scripts/check_browser.py`` after installing requirements-browser.txt.
Uses system Chrome on macOS when present; otherwise uses Playwright Chromium.
No bot token, real database, Telegram account, or external network is required.
The Telegram SDK request is replaced with an empty response: this checks browser
development mode, not the Telegram application's own native integration.
"""

import argparse
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from threading import Thread

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import expect, sync_playwright  # noqa: E402
from werkzeug.serving import WSGIRequestHandler, make_server  # noqa: E402

from roosters import create_app, rules  # noqa: E402
from roosters.config import Settings  # noqa: E402


class QuietRequests(WSGIRequestHandler):
    """Keep command output focused on assertions instead of polling logs."""

    def log_request(self, code="-", size="-"):
        pass


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0
        self.draw = 0.25

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def server_state(page):
    """Read with the browser's actual session; never invent a frontend balance."""
    return page.evaluate("""async () => {
        const token = sessionStorage.getItem('rooster.v1.token');
        const response = await fetch('/api/v1/state', {
            headers: {Authorization: `Bearer ${token}`}, cache: 'no-store'
        });
        if (!response.ok) throw new Error(`State request failed: ${response.status}`);
        return response.json();
    }""")


def wait_wallet(page, player):
    # Compare the localized display instead of parsing EN grouping commas as decimals.
    page.wait_for_function("""amount => document.getElementById('balance').textContent ===
        new Intl.NumberFormat(document.documentElement.lang === 'en' ? 'en-US' : 'ru-RU',
            {maximumFractionDigits: 2}).format(amount / 100)
    """, arg=player["balance_minor"])


def command(page, selector, operation):
    with page.expect_response(lambda response: response.url.endswith("/api/v1/" + operation)
                              and response.request.method == "POST") as received:
        page.locator(selector).click()
    response = received.value
    assert response.ok, "{} failed: {} {}".format(operation, response.status, response.text())
    if operation == "battle/start":
        assert set(response.request.post_data_json) == {"mode", "stake_minor", "expected_power"}
        assert response.request.post_data_json["mode"] == "bot"
    elif operation == "queue/join":
        assert set(response.request.post_data_json) == {"stake_minor"}
    result = response.json()
    wait_wallet(page, result["state"]["player"])
    return result


def tab(page, name):
    page.locator('[data-tab="{}"]'.format(name)).click()
    expect(page.locator('[data-tab="{}"]'.format(name))).to_have_attribute("aria-current", "page")


def refresh(page):
    """Use the same recovery path as returning to a visible Mini App."""
    with page.expect_response(lambda response: response.url.endswith("/api/v1/state")):
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def check_layout(page, label):
    dimensions = page.evaluate("""() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth
    })""")
    assert dimensions["document"] <= dimensions["viewport"] + 1, (label, dimensions)
    assert dimensions["body"] <= dimensions["viewport"] + 1, (label, dimensions)
    unnamed = page.locator("button:visible").evaluate_all("""buttons => buttons
        .filter(button => !(button.getAttribute('aria-label') || button.innerText).trim())
        .map(button => button.outerHTML)
    """)
    assert not unnamed, "Unlabelled buttons in {}: {}".format(label, unnamed)
    assert page.locator('[data-action="stance"], [data-stance], .stance-options').count() == 0


def check_battle_rules(battle):
    """The real API, countdown and tap budget use the new simpler rules."""
    assert battle["rules_version"] == "v8"
    assert battle["ends_at"] - battle["starts_at"] == 10
    assert battle["tap_cap"] == 90
    assert "stance" not in battle["you"]
    assert "stance" not in battle["opponent"]


def check_history_result(page, battle, language="ru"):
    """Completed results remain in history without a separate Last Battle card."""
    expect(page.locator(".last-battle-card, #last-result")).to_have_count(0)
    row = page.locator(".history-row").first
    expect(row).to_be_visible()
    expect(row).to_contain_text(("Победа" if battle["result"]["won"] else "Поражение")
                               if language == "ru" else ("Victory" if battle["result"]["won"] else "Defeat"))
    if not battle["opponent"]["is_bot"]:
        expect(row).to_contain_text(battle["opponent"]["name"])
    net = page.evaluate("""({minor, locale}) => (minor > 0 ? '+' : minor < 0 ? '−' : '') +
        new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(Math.abs(minor) / 100)
    """, {"minor": battle["result"]["net_minor"], "locale": "ru-RU" if language == "ru" else "en-US"})
    expect(row.locator(".history-reward")).to_contain_text(net)


def check_english_ui(page, state):
    """UI copy translates; names supplied by players/server stay proper names."""
    names = {state["player"]["name"]}
    for battle in [state.get("battle"), *state.get("history", [])]:
        if battle:
            names.update(battle[side]["name"] for side in ("you", "opponent"))
    names.update(page.locator(".leaderboard-name").evaluate_all("""elements => elements
        .map(element => Array.from(element.childNodes).filter(node => node.nodeType === Node.TEXT_NODE)
            .map(node => node.textContent).join('').trim())
    """))
    visible = page.locator("body").inner_text()
    # The avatar is the actual player's initial, not interface copy.
    visible = visible.replace("\n" + page.locator("#avatar").inner_text() + "\n", "\n")
    visible += page.locator("[aria-label]:visible, [title]:visible, [placeholder]:visible").evaluate_all("""elements => elements
        .filter(element => element.dataset.language !== 'ru')
        .map(element => ['aria-label', 'title', 'placeholder'].map(key => element.getAttribute(key) || '').join(' '))
        .join(' ')
    """)
    for name in sorted(names, key=len, reverse=True):
        visible = visible.replace(name, "")
    assert not re.search(r"[А-Яа-яЁё]", visible), "Untranslated English UI: {}".format(visible)
    expect(page.locator("#player-name")).to_have_text(state["player"]["name"])


def check_languages(page, artifacts):
    state = server_state(page)
    page.locator('[data-language="en"]').click()
    expect(page.locator("html")).to_have_attribute("lang", "en")
    check_history_result(page, state["battle"], language="en")
    page.reload(wait_until="networkidle")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    assert page.evaluate("localStorage.getItem('rooster.v1.language')") == "en"
    page.set_viewport_size({"width": 320, "height": 844})
    for name in ("arena", "gear", "roost", "leaderboard"):
        tab(page, name)
        if name == "leaderboard":
            expect(page.locator(".leaderboard-table")).to_be_visible()
        check_english_ui(page, state)
        check_layout(page, "320px English " + name)
    header = page.locator(".app-header").bounding_box()
    for language in ("ru", "en"):
        toggle = page.locator('[data-language="{}"]'.format(language))
        expect(toggle).to_be_visible()
        box = toggle.bounding_box()
        assert box["x"] >= header["x"] and box["x"] + box["width"] <= header["x"] + header["width"] + 1
    tab(page, "arena")
    check_history_result(page, state["battle"], language="en")
    page.screenshot(path=str(artifacts / "arena-english-mobile.png"), full_page=True, animations="disabled")
    page.locator('[data-language="ru"]').click()
    expect(page.locator("html")).to_have_attribute("lang", "ru")
    check_history_result(page, state["battle"])


def check_hit_feedback(page, clock, artifacts):
    """Probe real Web Animations without waiting for their short visual lifetime.

    Pausing a hit at 70 ms makes node/animation identity assertions deterministic
    through the actual pending-command render and a server-state refresh. This
    never pauses the application, its network traffic, or the battle clock.
    """
    page.emulate_media(reduced_motion="no-preference")
    started = command(page, '[data-action="start-free"]', "battle/start")
    battle = started["state"]["battle"]
    page.evaluate("""() => {
        window.__roosterHitAnimations = () => Array.from(document.querySelectorAll(
            '.fighter-motion, .hit-slash, .hit-spark'
        )).flatMap(element => element.getAnimations());
        window.__roosterHitSnapshot = () => ({
            moving: Array.from(document.querySelectorAll('.fighter-motion'))
                .flatMap(element => element.getAnimations()).length,
            accents: Array.from(document.querySelectorAll('.hit-slash, .hit-spark'))
                .flatMap(element => element.getAnimations()).length,
            particles: document.querySelectorAll('.hit-spark').length,
            slash: document.querySelectorAll('.hit-slash').length,
        });
        window.__roosterTapRequests = 0;
        const originalFetch = window.fetch;
        window.fetch = (...args) => {
            if (String(args[0]).endsWith('/api/v1/battle/tap')) window.__roosterTapRequests += 1;
            return originalFetch(...args);
        };
    }""")
    expect(page.locator('[data-action="battle-tap"]')).to_be_disabled()
    page.keyboard.press("Space")
    before_start = page.evaluate("""() => {
        document.querySelector('[data-action="battle-tap"]').click();
        return { ...window.__roosterHitSnapshot(), requests: window.__roosterTapRequests };
    }""")
    assert before_start["moving"] == before_start["accents"] == before_start["requests"] == 0

    clock.value = battle["starts_at"]
    refresh(page)
    expect(page.locator('[data-action="battle-tap"]')).to_be_enabled()
    # Ignore page entry and the existing 180 ms combat-start pulse before
    # measuring whether a combat hit changes the tap button's layout position.
    page.wait_for_function("() => getComputedStyle(document.getElementById('main')).transform === 'none'")
    page.wait_for_function("() => getComputedStyle(document.querySelector('.battle-tap')).transform === 'none'")
    button_before = page.evaluate('document.querySelector("[data-action=battle-tap]").getBoundingClientRect().toJSON()')
    immediate = page.evaluate("""() => {
        const requests = window.__roosterTapRequests;
        document.querySelector('[data-action="battle-tap"]').click();
        const animations = window.__roosterHitAnimations();
        window.__roosterPreviousHit = animations;
        for (const animation of animations) { animation.pause(); animation.currentTime = 70; }
        return { ...window.__roosterHitSnapshot(), networkDelta: window.__roosterTapRequests - requests };
    }""")
    assert immediate["moving"] == 2 and immediate["accents"] == 5, immediate
    assert immediate["networkDelta"] == 0, "Visual feedback must not wait for a tap request"

    rapid = page.evaluate("""() => {
        const results = [];
        for (let click = 0; click < 5; click++) {
            const previous = window.__roosterPreviousHit;
            document.querySelector('[data-action="battle-tap"]').click();
            const animations = window.__roosterHitAnimations();
            results.push({
                total: animations.length,
                replaced: animations.every(animation => !previous.includes(animation)),
                cancelled: previous.every(animation => animation.playState === 'idle'),
            });
            window.__roosterPreviousHit = animations;
        }
        window.__roosterHeldFighters = document.querySelector('.fighters');
        window.__roosterHeldAnimations = window.__roosterHitAnimations();
        for (const animation of window.__roosterHeldAnimations) {
            animation.pause(); animation.currentTime = 70;
        }
        return results;
    }""")
    assert all(item["total"] == 7 and item["replaced"] and item["cancelled"] for item in rapid), rapid
    expect(page.locator(".tap-score")).to_contain_text("6 / 90")

    def held_hit():
        return page.evaluate("""() => ({
            sameNode: document.querySelector('.fighters') === window.__roosterHeldFighters,
            sameAnimations: window.__roosterHeldAnimations.every(animation =>
                window.__roosterHitAnimations().includes(animation) && animation.playState === 'paused'
                && Math.abs(animation.currentTime - 70) < 1
            ),
            ...window.__roosterHitSnapshot(),
        })""")

    pending_render = held_hit()
    assert pending_render["sameNode"] and pending_render["sameAnimations"], pending_render
    page.evaluate("window.__roosterBeforePollCard = document.querySelector('.battle-card')")
    refresh(page)
    page.wait_for_function("() => document.querySelector('.battle-card') !== window.__roosterBeforePollCard")
    polled = held_hit()
    assert polled["sameNode"] and polled["sameAnimations"], polled
    assert polled["particles"] == 4 and polled["slash"] == 1, polled
    requests_before_language = page.evaluate("window.__roosterTapRequests")
    for language in ("en", "ru"):
        page.locator('[data-language="{}"]'.format(language)).click()
        expect(page.locator("html")).to_have_attribute("lang", language)
        translated_hit = held_hit()
        assert translated_hit["sameNode"] and translated_hit["sameAnimations"], translated_hit
        unchanged = server_state(page)
        assert unchanged["battle"]["id"] == battle["id"]
        assert unchanged["battle"]["you"]["taps"] == 6
        expect(page.locator('[data-fighter-name="you"]')).to_have_text(battle["you"]["name"])
        assert page.evaluate("window.__roosterTapRequests") == requests_before_language
        if language == "en":
            check_english_ui(page, unchanged)
    displacement = page.evaluate("""() => new DOMMatrixReadOnly(getComputedStyle(
        document.querySelector('[data-fighter="you"]')
    ).transform).e""")
    assert displacement > 10, "The CSS-variable lunge must actually move the rooster: {}".format(displacement)
    assert page.evaluate("""() => new DOMMatrixReadOnly(getComputedStyle(
        document.querySelector('[data-fighter="opponent"] .rooster-art')
    ).transform).a === -1"""), "Opponent artwork must keep facing the player's rooster"
    # Polling replaces controls; resolve and measure in the same JS task.
    button_after = page.evaluate('document.querySelector("[data-action=battle-tap]").getBoundingClientRect().toJSON()')
    assert abs(button_after["y"] - button_before["y"]) < 1, (button_before, button_after)
    page.screenshot(path=str(artifacts / "battle-hit-mobile.png"), full_page=True, animations="allow")

    # The preference is checked on every click, so it also works if the player
    # changes their accessibility setting without reopening the Mini App.
    page.emulate_media(reduced_motion="reduce")
    reduced = page.evaluate("""() => {
        document.querySelector('[data-action="battle-tap"]').click();
        const animations = window.__roosterHitAnimations();
        const frames = animations.flatMap(animation => animation.effect.getKeyframes());
        for (const animation of animations) { animation.pause(); animation.currentTime = 50; }
        return {
            ...window.__roosterHitSnapshot(),
            onlyOpacity: frames.every(frame => !Object.hasOwn(frame, 'transform')),
        };
    }""")
    assert reduced["moving"] == 0 and reduced["accents"] == 1 and reduced["onlyOpacity"], reduced
    expect(page.locator(".tap-score")).to_contain_text("7 / 90")
    page.screenshot(path=str(artifacts / "battle-hit-reduced-mobile.png"), full_page=True, animations="allow")

    # One physical press, including key repeat and a focused button's native
    # keyup, must produce exactly one accepted tap and the same immediate hit.
    page.locator('[data-action="battle-tap"]').focus()
    page.evaluate("window.__roosterBeforeSpace = window.__roosterHitAnimations()")
    page.keyboard.down("Space")
    assert page.evaluate("""() => window.__roosterHitAnimations().length === 1 &&
        window.__roosterHitAnimations().every(animation => !window.__roosterBeforeSpace.includes(animation))
    """), "Space must immediately start a new combat animation"
    for _ in range(4):
        page.keyboard.down("Space")
    expect(page.locator(".tap-score")).to_contain_text("8 / 90")
    page.keyboard.up("Space")
    page.wait_for_timeout(450)  # Flush any erroneous second tap caused by keyup.
    assert server_state(page)["battle"]["you"]["taps"] == 8

    page.evaluate("() => { document.activeElement.blur(); window.scrollTo(0, 0); }")
    page.keyboard.press("Space")
    expect(page.locator(".tap-score")).to_contain_text("9 / 90")
    assert page.evaluate("window.scrollY") == 0, "Combat Space must not scroll the page"

    # Temporary native controls keep their own Space behavior, without creating
    # combat taps. Keep them outside main so the normal server poll cannot erase them.
    page.evaluate("""() => {
        const controls = document.createElement('div');
        controls.id = 'rooster-keyboard-controls';
        controls.style.cssText = 'position:fixed;top:0;left:0;z-index:100';
        controls.innerHTML = '<input id="rooster-space-input"><div id="rooster-space-editable" contenteditable="true"></div><button id="rooster-space-control">Native control</button>';
        document.body.append(controls);
        window.__roosterNativeActivations = 0;
        controls.querySelector('button').addEventListener('click', () => window.__roosterNativeActivations++);
    }""")
    page.locator("#rooster-space-input").focus()
    page.keyboard.press("Space")
    expect(page.locator("#rooster-space-input")).to_have_value(" ")
    page.locator("#rooster-space-editable").focus()
    page.keyboard.press("Space")
    assert page.locator("#rooster-space-editable").text_content().replace("\u00a0", " ") == " "
    page.locator("#rooster-space-control").focus()
    page.keyboard.press("Space")
    assert page.evaluate("window.__roosterNativeActivations") == 1
    page.wait_for_timeout(450)
    assert server_state(page)["battle"]["you"]["taps"] == 9
    page.evaluate("document.getElementById('rooster-keyboard-controls').remove()")

    # Let the local countdown cross the deadline before the next 2-second poll.
    # The active DOM still exists, but an expired battle must reject visual hits.
    clock.value = battle["ends_at"] - 0.2
    refresh(page)
    expect(page.locator("#battle-phase")).to_have_text("БОЙ ЗАВЕРШЁН")
    expect(page.locator('[data-action="battle-tap"]')).to_be_disabled()
    requests_before_expired_space = page.evaluate("window.__roosterTapRequests")
    page.keyboard.press("Space")
    expired = page.evaluate("""() => {
        const requests = window.__roosterTapRequests;
        document.querySelector('[data-action="battle-tap"]').click();
        return { ...window.__roosterHitSnapshot(), networkDelta: window.__roosterTapRequests - requests };
    }""")
    assert expired["moving"] == expired["accents"] == expired["networkDelta"] == 0, expired
    assert page.evaluate("window.__roosterTapRequests") == requests_before_expired_space
    clock.value = battle["ends_at"] + 1
    refresh(page)
    expect(page.locator(".history-row")).not_to_have_count(0)


def exercise(browser, base_url, clock, artifacts):
    errors = []

    def player_context(user_id, name):
        context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
        context.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(
            status=200, content_type="application/javascript", body="/* Local development browser test. */"))
        context.add_init_script("""if (!localStorage.getItem('rooster.v1.identity')) {
            localStorage.setItem('rooster.v1.identity', JSON.stringify(%s));
        }""" % json.dumps({"user_id": user_id, "name": name}))
        context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")
        expect(page.locator("#navigation")).to_be_visible()
        expect(page.locator("#player-name")).to_have_text(name)
        expect(page.locator("html")).to_have_attribute("lang", "ru")
        expect(page.locator('[data-language="ru"]')).to_have_attribute("aria-pressed", "true")
        assert server_state(page)["player"]["id"] == "dev:" + user_id
        return context, page

    context_a, a = player_context("browser_alice", "Алиса Арены")
    try:
        initial = server_state(a)
        assert initial["player"]["balance_minor"] == 42000
        assert initial["presence"]["online"] == 0
        assert initial["catalog"]["battle"]["duration"] == 10
        assert initial["catalog"]["battle"]["tap_cap"] == 90
        assert "energy" not in initial["economy"] and "medals" not in initial["player"]
        wait_wallet(a, initial["player"])
        free_button = a.locator('[data-action="start-free"]')
        expect(free_button).to_be_visible()
        assert free_button.bounding_box()["y"] + free_button.bounding_box()["height"] < 754
        a.screenshot(path=str(artifacts / "onboarding-mobile.png"), full_page=True, animations="disabled")

        # The free introduction still pays its fixed reward when the user loses.
        clock.draw = 0.99
        started = command(a, '[data-action="start-free"]', "battle/start")
        battle = started["state"]["battle"]
        check_battle_rules(battle)
        assert battle["wager"] == {"stake_minor": 0, "win_payout_minor": 1500}
        assert started["state"]["player"]["balance_minor"] == 42000
        assert not started["state"]["economy"]["first_free_battle_available"]
        expect(a.locator("#roulette")).to_be_visible()
        expect(a.locator("#battle-prize")).to_contain_text("15")
        expect(a.locator('[data-action="battle-tap"]')).to_be_disabled()
        a.reload(wait_until="networkidle")
        restored = server_state(a)["battle"]
        assert restored["id"] == battle["id"] and restored["opponent"] == battle["opponent"]
        clock.value = battle["starts_at"]
        refresh(a)
        expect(a.locator("#battle-phase")).to_have_text("БОЙ ИДЁТ")
        expect(a.locator("#roulette")).to_be_hidden()
        expect(a.locator("#opponent-power")).to_contain_text(str(battle["opponent"]["power"]))
        expect(a.locator('[data-action="battle-tap"]')).to_be_enabled()
        box = a.locator('[data-action="battle-tap"]').bounding_box()
        expect(a.locator("#navigation")).to_be_hidden()
        assert box["y"] >= 0 and box["y"] + box["height"] <= a.viewport_size["height"], box
        a.screenshot(path=str(artifacts / "battle-mobile.png"), full_page=True, animations="disabled")
        with a.expect_response(lambda response: response.url.endswith("/api/v1/battle/tap")) as received:
            a.evaluate("""() => { for (let i = 0; i < 90; i++) document.querySelector('[data-action="battle-tap"]').click(); }""")
        tapped = received.value.json()
        assert tapped["state"]["battle"]["current_win_probability"] > battle["current_win_probability"]
        expect(a.locator('#battle-odds')).to_be_visible()
        assert tapped["result"]["accepted"] == 90
        assert received.value.request.post_data_json["taps"] == 90
        expect(a.locator('[data-action="battle-tap"]')).to_be_disabled()
        # Global navigation stays suppressed even after reaching the tap cap.
        expect(a.locator("#navigation")).to_be_hidden()
        clock.value = battle["ends_at"] + 1
        refresh(a)
        expect(a.locator("#navigation")).to_be_visible()
        expect(a.locator(".history-row")).not_to_have_count(0)
        finished = server_state(a)
        assert not finished["battle"]["result"]["won"]
        assert finished["battle"]["result"]["payout_minor"] == 1500
        assert finished["player"]["balance_minor"] == 43500
        check_history_result(a, finished["battle"])
        toast = a.locator('#battle-result-toast')
        expect(toast).to_be_visible()
        a.screenshot(path=str(artifacts / 'result-toast-mobile.png'), full_page=True)
        a.wait_for_timeout(1100)
        progress = a.locator('#result-close-progress').evaluate('node => Number(node.style.strokeDashoffset)')
        assert 20 < progress < 90, progress
        refresh(a)  # Must not restart the four-second countdown.
        expect(toast).to_be_hidden(timeout=3500)
        check_history_result(a, finished["battle"])
        refresh(a)
        expect(toast).to_be_hidden()
        # Old result-card preferences cannot remove the completed battle from history.
        a.evaluate("id => sessionStorage.setItem('rooster.v1.dismissedBattle', id)", battle["id"])
        a.reload(wait_until="networkidle")
        assert server_state(a)["player"]["balance_minor"] == 43500
        check_history_result(a, finished["battle"])
        assert a.locator('[data-action="start-free"]').count() == 0
        assert a.locator('[data-action="start-practice"]').count() == 0

        tab(a, "roost")
        assert a.locator('[data-action="train"]').count() == 0
        daily = command(a, '[data-action="claim-daily"]', "claim/daily")
        assert daily["result"]["payout_minor"] == 17000
        assert daily["state"]["player"]["balance_minor"] == 60500
        expect(a.locator('[data-action="claim-daily"]')).to_be_disabled()
        clock.advance(3 * 3600)
        a.reload(wait_until="networkidle")
        passive = command(a, '[data-action="claim-passive"]', "claim/passive")
        assert passive["result"]["payout_minor"] == 36000
        tab(a, "gear")
        upgraded = command(a, '[data-action="upgrade"][data-slot="sword"]', "gear/upgrade")
        assert upgraded["state"]["player"]["power"] == 110
        purchased = command(a, '[data-action="breed"][data-breed="copper"]', "breed/buy")
        assert purchased["state"]["player"]["power"] == 132
        a.reload(wait_until="networkidle")
        assert server_state(a)["player"]["balance_minor"] == 38500

        # A lost start response must preserve the already charged stake, chosen
        # opponent and immutable payout; reloading cannot reroll that battle.
        tab(a, "arena")
        before_paid = server_state(a)["player"]["balance_minor"]
        def lose_response(route):
            response = route.fetch()
            assert response.ok
            route.abort("failed")
        context_a.route("**/api/v1/battle/start", lose_response, times=1)
        a.locator('[data-action="start-bot"]').click()
        expect(a.locator('[data-action="retry"]')).to_be_visible()
        committed = server_state(a)
        assert committed["player"]["balance_minor"] == before_paid - 1000
        replay = command(a, '[data-action="retry"]', "battle/start")
        paid = replay["state"]["battle"]
        assert paid["id"] == committed["battle"]["id"]
        assert paid["wager"] == committed["battle"]["wager"]
        assert paid["opponent"] == committed["battle"]["opponent"]
        check_battle_rules(paid)
        # Gameplay reserves the viewport for this battle; the previous result
        # remains in server history while the removed result card stays absent.
        expect(a.locator(".last-battle-card")).to_have_count(0)
        assert committed["history"][0]["id"] == finished["battle"]["id"]
        a.reload(wait_until="networkidle")
        assert server_state(a)["battle"]["id"] == paid["id"]
        expect(a.locator(".last-battle-card")).to_have_count(0)
        clock.value = paid["ends_at"] + 1
        refresh(a)
        expect(a.locator(".history-row")).not_to_have_count(0)
        lost = server_state(a)
        assert not lost["battle"]["result"]["won"]
        assert lost["battle"]["result"]["payout_minor"] == 0
        assert lost["battle"]["result"]["net_minor"] == -1000
        assert lost["player"]["balance_minor"] == before_paid - 1000
        check_history_result(a, lost["battle"])

        # Different stakes match immediately; each personal prize follows the final odds.
        a.locator('[data-action="stake"][data-stake-minor="2500"]').click()
        a.locator('[data-action="arena-mode"][data-mode="online"]').click()
        before_queue = server_state(a)["player"]["balance_minor"]
        queued = command(a, '[data-action="queue-join"]', "queue/join")
        assert queued["state"]["queue"]["stake_minor"] == 2500
        assert queued["state"]["player"]["balance_minor"] == before_queue
        expect(a.locator(".queue-card")).to_contain_text("25")
        expect(a.locator(".last-battle-card, #last-result")).to_have_count(0)
        assert server_state(a)["history"][0]["id"] == lost["battle"]["id"]
        cancelled = command(a, '[data-action="queue-leave"]', "queue/leave")
        assert cancelled["state"]["queue"] is None
        context_b, b = player_context("browser_bob", "Боб Бойцовский")
        try:
            clock.draw = 0.25
            refresh(a)
            present = server_state(a)["presence"]
            assert present["online"] == 0 and present["development_online"] == 2
            balances_before_pvp = [server_state(page)["player"]["balance_minor"] for page in (a, b)]
            command(a, '[data-action="queue-join"]', "queue/join")
            matched = command(b, '[data-action="queue-join"]', "queue/join")
            assert matched["result"]["matched"]
            expect(a.locator(".battle-card")).to_be_visible()
            expect(b.locator(".battle-card")).to_be_visible()
            pvp_a, pvp_b = server_state(a)["battle"], server_state(b)["battle"]
            check_battle_rules(pvp_a)
            assert pvp_a["id"] == pvp_b["id"]
            for battle, stake in ((pvp_a, 2500), (pvp_b, 1000)):
                chance = rules.win_probability(battle["you"]["power"], battle["opponent"]["power"], 0, 0)
                assert battle["wager"] == {"stake_minor": stake, "win_payout_minor": rules.online_payout(stake, chance)}
            expect(a.locator(".last-battle-card")).to_have_count(0)
            clock.value = pvp_a["ends_at"] + 1
            refresh(a)
            refresh(b)
            expect(a.locator(".history-row")).not_to_have_count(0)
            expect(b.locator(".history-row")).not_to_have_count(0)
            settled_a, settled_b = server_state(a), server_state(b)
            assert settled_a["battle"]["result"]["won"] != settled_b["battle"]["result"]["won"]
            for before, settled, stake in zip(balances_before_pvp, (settled_a, settled_b), (2500, 1000)):
                battle = settled["battle"]
                chance = rules.win_probability(battle["you"]["power"], battle["opponent"]["power"],
                                               battle["you"]["taps"], battle["opponent"]["taps"])
                expected_payout = rules.online_payout(stake, chance) if battle["result"]["won"] else 0
                assert settled["battle"]["result"]["payout_minor"] == expected_payout
                assert settled["player"]["balance_minor"] == before - stake + expected_payout
            assert settled_a["player"]["pvp_wins"] == settled_b["player"]["pvp_wins"] == 0
            check_history_result(a, settled_a["battle"])
            check_history_result(b, settled_b["battle"])
            winner = a if settled_a["battle"]["result"]["won"] else b
            expect(winner.locator('#battle-result-toast')).to_be_visible()
            expect(winner.locator('#battle-result-toast h2')).to_have_text('Победа')
            winner.screenshot(path=str(artifacts / 'victory-toast-mobile.png'), full_page=True)
            winner.locator('[data-action="close-result"]').click()
            expect(winner.locator('#battle-result-toast')).to_be_hidden()
            refresh(winner)
            expect(winner.locator('#battle-result-toast')).to_be_hidden()

        finally:
            context_b.close()

        if a.locator('[data-action="close-result"]').is_visible():
            a.locator('[data-action="close-result"]').click()
        tab(a, "gear")
        before_lost = server_state(a)["player"]
        context_a.route("**/api/v1/gear/upgrade", lose_response, times=1)
        a.locator('[data-action="upgrade"][data-slot="helmet"]').click()
        expect(a.locator('[data-action="retry"]')).to_be_visible()
        charged = server_state(a)["player"]
        assert charged["balance_minor"] == before_lost["balance_minor"] - 8000
        replay = command(a, '[data-action="retry"]', "gear/upgrade")
        assert replay["state"]["player"]["balance_minor"] == charged["balance_minor"]
        assert replay["state"]["player"]["gear"]["helmet"] == 1
        notice = a.locator('[data-action="dismiss-notice"]')
        if notice.count():
            notice.click()

        for width in (320, 390, 1280):
            a.set_viewport_size({"width": width, "height": 844 if width < 500 else 1000})
            for name in ("arena", "gear", "roost", "leaderboard"):
                tab(a, name)
                if name == "leaderboard":
                    expect(a.locator(".leaderboard-table")).to_be_visible()
                    a.locator('[data-action="ranking"][data-ranking="pvp_wins"]').click()
                    expect(a.locator('[data-ranking="pvp_wins"]')).to_have_attribute("aria-pressed", "true")
                    a.locator('[data-action="ranking"][data-ranking="power"]').click()
                check_layout(a, "{}px {}".format(width, name))
            tab(a, "arena")
            if width in (390, 1280):
                filename = "arena-mobile.png" if width == 390 else "arena-desktop.png"
                a.screenshot(path=str(artifacts / filename), full_page=True, animations="disabled")
        check_languages(a, artifacts)
        context_fx, fx = player_context("browser_feedback", "Петух с характером")
        try:
            check_hit_feedback(fx, clock, artifacts)
        except BaseException:
            fx.screenshot(path=str(artifacts / "battle-hit-failure.png"), full_page=True, animations="allow")
            raise
        finally:
            context_fx.close()
        assert not errors, "Browser JavaScript errors: {}".format(errors)
    except BaseException:
        a.screenshot(path=str(artifacts / "arena-failure.png"), full_page=True, animations="disabled")
        raise
    finally:
        context_a.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix="roosters-browser-") as temporary:
        app = create_app(Settings(app_env="test", allow_dev_auth=True, secret_key="test" * 16,
                                  database_path=str(Path(temporary) / "game.sqlite3")),
                         clock=clock, random_float=lambda: clock.draw)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                options = {"headless": True}
                if Path(args.chrome).exists():
                    options["executable_path"] = args.chrome
                browser = playwright.chromium.launch(**options)
                try:
                    exercise(browser, "http://127.0.0.1:{}".format(server.server_port), clock, args.artifacts_dir)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print("Browser checks passed: first free loss reward, single currency, 10s/90 unthrottled taps, "
          "fixed roulette reload, daily/passive, upgrades/breeds, lost paid start/purchase retry, "
          "paid loss, different-stake PvP/own-stake payout, two rankings, instant/retriggered hit animation, "
          "animation survives command/poll renders, reduced motion, phase guards, "
          "Space press/repeat/keyup/scroll and native-control guards, "
          "history through reload and queue with no Last Battle card, "
          "RU/EN persistence and complete screens, language switching preserves combat feedback/taps, "
          "320/390/1280 layouts, zero JS errors.")
    print("Screenshots: {}".format(args.artifacts_dir))


if __name__ == "__main__":
    main()
