"""Live rooster art, gear and frozen battle appearances on a disposable DB.

Run: python -m scripts.check_rooster_art_browser
Uses real API commands and Chrome rendering; Telegram insets/theme are simulated.
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters.config import Settings
from scripts.check_battle_browser import assert_controls
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, server_state, tab
from scripts.check_gear_browser import seed
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent
BREEDS = ("yard", "copper", "storm", "ember")
SLOTS = ("helmet", "armor", "sword")
TIER_LEVELS = (0, 1, 3, 5, 8, 10)
SIZES = ((320, 568), (360, 560), (390, 844), (430, 932))
ARENA_ART = ".arena-scene svg.rooster-art"


def context_page(browser, url, identity, errors, *, size=(390, 844), language="ru", scheme="dark"):
    context = browser.new_context(viewport={"width": size[0], "height": size[1]},
                                  color_scheme=scheme, reduced_motion="reduce", has_touch=True)
    context.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(
        status=200, content_type="application/javascript", body=BRIDGE.replace("SCHEME", json.dumps(scheme))))
    context.add_init_script("""let artTime = Date.now(); Date.now = () => artTime;
        window.artAdvanceTime = milliseconds => { artTime += milliseconds; };""")
    context.add_init_script("localStorage.setItem('rooster.v1.identity', %s)" % json.dumps(json.dumps(
        {"user_id": identity, "name": "Боец с очень длинным именем"})))
    context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
    context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url, wait_until="networkidle")
    page.evaluate("shellTheme(%s)" % json.dumps(scheme))
    return context, page


def returning_player(app, page):
    """Fixture only: expose the compact returning Arena without an extra match."""
    pid = server_state(page)["player"]["id"]
    with app.extensions["database"].transaction() as db:
        db.execute("UPDATE players SET first_battle_used=1 WHERE id=?", (pid,))
    refresh(page)
    expect(page.locator(".arena-returning")).to_be_visible()


def expected_tier(level):
    return sum(level >= threshold for threshold in TIER_LEVELS[1:])


def check_art(page, selector, fighter, label):
    """Check the live server selection and decode actual raster layer resources."""
    art = page.locator(selector)
    expect(art).to_have_count(1)
    expect(art).to_have_attribute("data-breed", fighter["breed_id"])
    for slot in SLOTS:
        expect(art).to_have_attribute(f"data-{slot}-tier", str(expected_tier(fighter["gear"][slot])))
    expect(art).to_have_attribute("role", "img")
    assert (art.get_attribute("aria-label") or "").strip(), label
    assert art.locator("text").count() == 0, label + ": game labels must remain live DOM"
    layers = art.locator("image.rooster-layer")
    assert layers.count() >= 1, label + ": no raster rooster layers"
    sources = layers.evaluate_all("nodes => nodes.map(node => node.href.baseVal)")
    # Equipment variants share atlas files: the crop and placement identify the
    # actual rendered material, not the URL alone.
    frames = layers.evaluate_all("""nodes => nodes.map(node => ({source: node.href.baseVal,
        frame: Object.fromEntries(['data-slot', 'viewBox', 'x', 'y', 'width', 'height']
            .map(key => [key, node.parentElement.getAttribute(key)]))}))""")
    decoded = page.evaluate("""async sources => Promise.all(sources.map(source => new Promise(resolve => {
        const image = new Image();
        image.onload = () => resolve({source, width: image.naturalWidth, height: image.naturalHeight});
        image.onerror = () => resolve({source, width: 0, height: 0});
        image.src = source;
    })))""", sources)
    assert all(item["width"] > 0 and item["height"] > 0 for item in decoded), (label, decoded)
    geometry = page.evaluate("""selector => {
        const node = document.querySelector(selector);
        const box = node.getBoundingClientRect();
        return {width: box.width, height: box.height, left: box.left, right: box.right,
            hidden: getComputedStyle(node).visibility === 'hidden' || getComputedStyle(node).display === 'none'};
    }""", selector)
    assert geometry["width"] >= 65 and geometry["height"] >= 65 and not geometry["hidden"], (label, geometry)
    assert geometry["left"] >= 0 and geometry["right"] <= page.viewport_size["width"] + 1, (label, geometry)
    return {"breed": fighter["breed_id"], "gear": dict(fighter["gear"]), "sources": sources, "frames": frames}


def arena_art(page, label):
    state = server_state(page)
    appearance = check_art(page, ARENA_ART, state["player"], label)
    progress = page.locator(".arena-scene [role=progressbar]")
    expect(progress).to_have_attribute("aria-valuenow", str(state["player"]["xp_in_level"]))
    expect(progress).to_have_attribute("aria-valuemax", str(state["player"]["xp_to_next"]))
    return appearance


def battle_art(page, battle, label):
    return {side: check_art(page, f'[data-fighter="{side}"] svg.rooster-art', battle[side], label + " " + side)
            for side in ("you", "opponent")}


def save_view(page, artifacts, name, *, arena=False):
    check_layout(page, name)
    if arena:
        page.evaluate("scrollTo(0, 0)")
    else:
        visibility = page.evaluate("""() => {
            const overview = document.querySelector('.battle-overview');
            overview.scrollTop = 0;
            return {overview: overview.getBoundingClientRect().toJSON(),
                avatars: [...overview.querySelectorAll('.fighter-avatar')]
                    .map(node => node.getBoundingClientRect().toJSON()),
                artworks: [...overview.querySelectorAll('svg.rooster-art')]
                    .map(node => node.getBoundingClientRect().toJSON())};
        }""")
        assert len(visibility["avatars"]) == 2, (name, visibility)
        assert len(visibility["artworks"]) == 2, (name, visibility)
        for art_box in [*visibility["avatars"], *visibility["artworks"]]:
            visible = (art_box["top"] >= visibility["overview"]["top"] - 1
                       and art_box["bottom"] <= visibility["overview"]["bottom"] + 1)
            if not visible:
                page.screenshot(path=str(artifacts / f"{name}.png"), animations="disabled")
            assert visible, (
                name + ": rooster body requires scrolling before it can be seen", visibility)
    page.screenshot(path=str(artifacts / f"{name}.png"), animations="disabled")


def retain_battle_nodes(page):
    page.evaluate("""() => { window.artRetained = [...document.querySelectorAll(
        '.fighters, .fighter-motion, .fighters svg.rooster-art, .battle-tap')]; }""")


def assert_retained(page, label):
    assert page.evaluate("""() => {
        const current = [...document.querySelectorAll('.fighters, .fighter-motion, .fighters svg.rooster-art, .battle-tap')];
        return current.length === artRetained.length && current.every((node, index) => node === artRetained[index] && node.isConnected);
    }"""), label + ": polling or localization replaced fighter/tap DOM"


def check_frozen_battle(page, battle, label):
    original = battle_art(page, battle, label)
    retain_battle_nodes(page)
    refresh(page)
    assert_retained(page, label + " refresh")
    for language in ("en", "ru"):
        page.locator(f'[data-language="{language}"]').click()
        assert_retained(page, label + " " + language)
        assert battle_art(page, battle, label) == original
    # Exercise the real 2s polling scheduler, while holding backend game time.
    with page.expect_response(lambda response: response.url.endswith("/api/v1/state")):
        page.evaluate("artAdvanceTime(2100)")
    page.wait_for_load_state("networkidle")
    assert_retained(page, label + " scheduled poll")
    assert battle_art(page, battle, label) == original
    page.reload(wait_until="networkidle")
    assert server_state(page)["battle"]["id"] == battle["id"]
    assert battle_art(page, battle, label + " reload") == original


def exercise_arena(browser, url, app, artifacts, errors):
    appearances = {}
    for index, size in enumerate(SIZES):
        for scheme in ("light", "dark"):
            for language in ("ru", "en"):
                label = f"arena-{size[0]}x{size[1]}-{scheme}-{language}"
                context, page = context_page(browser, url, label, errors, size=size, scheme=scheme, language=language)
                try:
                    returning_player(app, page)
                    gear = {"helmet": TIER_LEVELS[(index + 2) % 6], "armor": TIER_LEVELS[index], "sword": TIER_LEVELS[index + 2]}
                    seed(app, page, breed_id=BREEDS[index], gear=gear, owned_breeds=list(BREEDS))
                    appearances[label] = arena_art(page, label)
                    save_view(page, artifacts, label, arena=True)
                    # Natural scrolling must still bring the real fight action above navigation.
                    page.locator(".arena-fight").evaluate("node => node.scrollIntoView({block:'center', behavior:'instant'})")
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    fight = page.evaluate("""() => {
                        const node = document.querySelector('.arena-fight');
                        node.scrollIntoView({block:'center', behavior:'instant'});
                        const box = node.getBoundingClientRect();
                        const hit = document.elementFromPoint(box.x + box.width/2, box.y + box.height/2);
                        return {height: box.height, box: box.toJSON(), reachable: node.contains(hit), hit: hit?.tagName, scrollY};
                    }""")
                    if fight["height"] < 44 or not fight["reachable"]:
                        page.screenshot(path=str(artifacts / f"{label}-covered-action.png"))
                    assert fight["height"] >= 44 and fight["reachable"], (label + ": Fight action covered", fight)
                finally:
                    context.close()
                print("Passed:", label, flush=True)
    context, page = context_page(browser, url, "art-progression", errors)
    try:
        appearances["first-visit"] = arena_art(page, "first visit")
        returning_player(app, page)
        # All breeds at each tier catches a missing/cross-wired breed asset.
        for breed in BREEDS:
            for level in TIER_LEVELS:
                seed(app, page, breed_id=breed, gear={slot: level for slot in SLOTS})
                label = f"progression-{breed}-{level}"
                appearances[label] = arena_art(page, label)
                save_view(page, artifacts, label, arena=True)
            print("Passed: all appearance tiers for", breed, flush=True)
        # Independently changing one slot must leave the other tier choices alone.
        zero = dict.fromkeys(SLOTS, 0)
        seed(app, page, breed_id="copper", gear=zero)
        baseline = arena_art(page, "independent baseline")
        for slot in SLOTS:
            last_frames = baseline["frames"]
            for level in TIER_LEVELS[1:]:
                gear = {**zero, slot: level}
                seed(app, page, gear=gear)
                label = f"independent-{slot}-{level}"
                result = arena_art(page, label)
                assert result["frames"] != last_frames, label + ": changed tier has unchanged art"
                last_frames = result["frames"]
                appearances[label] = result
            print("Passed: independent", slot, "tiers", flush=True)
        seed(app, page, gear={"helmet": 10, "armor": 1, "sword": 5})
        appearances["mixed-gear"] = arena_art(page, "mixed gear")
        save_view(page, artifacts, "arena-mixed-helmet10-armor1-sword5", arena=True)
        # Buy the actual first sword and breed through the existing controls.
        seed(app, page, breed_id="yard", gear=zero, owned_breeds=["yard"], balance_minor=100000)
        tab(page, "gear")
        upgraded = command(page, '[data-action="upgrade"][data-slot="sword"]', "gear/upgrade")
        assert upgraded["state"]["player"]["gear"]["sword"] == 1
        bought = command(page, '[data-action="breed"][data-breed="copper"]', "breed/buy")
        assert bought["state"]["player"]["breed_id"] == "copper"
        tab(page, "arena")
        appearances["purchased"] = arena_art(page, "after purchase")
        page.reload(wait_until="networkidle")
        assert arena_art(page, "purchase reload") == appearances["purchased"]
        save_view(page, artifacts, "arena-purchased-copper-sword1", arena=True)
        print("Passed: mixed gear, real purchases and reload", flush=True)
    finally:
        context.close()
    return appearances


def exercise_battles(browser, url, app, clock, artifacts, errors):
    appearances = {}
    # A real bot battle in each mobile viewport; inspect both languages/themes.
    for index, size in enumerate(SIZES):
        label = f"bot-{size[0]}x{size[1]}"
        context, page = context_page(browser, url, label, errors, size=size)
        try:
            seed(app, page, breed_id=BREEDS[index], gear={"helmet": 10, "armor": 1, "sword": 5})
            arena = arena_art(page, label + " arena")
            battle = command(page, '[data-action="start-free"]', "battle/start")["state"]["battle"]
            assert battle["opponent"]["is_bot"]
            appearances[label] = battle_art(page, battle, label)
            assert appearances[label]["you"] == arena, label + ": your Arena and battle art differ"
            expect(page.locator(".battle-tap")).to_be_disabled()
            save_view(page, artifacts, label + "-preparing")
            clock.value = battle["starts_at"]
            refresh(page)
            expect(page.locator(".battle-tap")).to_be_enabled()
            for scheme in ("light", "dark"):
                page.evaluate("shellTheme(%s)" % json.dumps(scheme))
                for language in ("ru", "en"):
                    page.locator(f'[data-language="{language}"]').click()
                    assert battle_art(page, battle, label) == appearances[label]
                    assert_controls(page, label + scheme + language, top_inset=40, bottom_inset=30)
                    save_view(page, artifacts, label + f"-{scheme}-{language}")
            if index == 0:
                check_frozen_battle(page, battle, label)
                retain_battle_nodes(page)
                with page.expect_response(lambda response: response.url.endswith("/api/v1/battle/tap")) as tapped:
                    page.locator(".battle-tap").tap()
                assert tapped.value.json()["state"]["battle"]["you"]["taps"] == 1
                assert_retained(page, label + " tap")
                assert page.locator(".fighter-motion, .hit-spark").evaluate_all(
                    "nodes => nodes.every(node => node.getAnimations().every(animation => animation.playState !== 'running'))"
                ), "reduced motion should suppress hit movement"
                assert page.locator(".hit-slash").evaluate_all("""nodes => nodes.every(node =>
                    node.getAnimations().every(animation => animation.effect.getKeyframes()
                        .every(frame => !frame.transform || frame.transform === 'none')))"""), "reduced hit accent must not move"
                # Fixture mutation proves the renderer reads battle snapshots, not today's wardrobe.
                seed(app, page, breed_id="ember", gear=dict.fromkeys(SLOTS, 0))
                assert battle_art(page, battle, label + " wardrobe changed") == appearances[label]
                check_frozen_battle(page, battle, label + " frozen snapshot")
            clock.value = battle["ends_at"] + 1
            refresh(page)
        finally:
            context.close()
        print("Passed:", label, "theme/language matrix and battle snapshots", flush=True)

    contexts = []
    try:
        pages = []
        choices = (("copper", {"helmet": 10, "armor": 1, "sword": 5}),
                   ("storm", {"helmet": 1, "armor": 8, "sword": 10}))
        for index, (breed, gear) in enumerate(choices):
            context, page = context_page(browser, url, f"art-pvp-{index}", errors,
                                         size=SIZES[index + 1], language=("ru", "en")[index])
            contexts.append(context)
            pages.append(page)
            seed(app, page, breed_id=breed, gear=gear)
        command(pages[0], '[data-action="queue-join"]', "queue/join")
        command(pages[1], '[data-action="queue-join"]', "queue/join")
        refresh(pages[0])
        battles = [server_state(page)["battle"] for page in pages]
        assert battles[0]["id"] == battles[1]["id"]
        for index, page in enumerate(pages):
            assert not battles[index]["opponent"]["is_bot"]
            appearances[f"pvp-{index}"] = battle_art(page, battles[index], f"pvp-{index}")
        assert appearances["pvp-0"]["you"] == appearances["pvp-1"]["opponent"]
        assert appearances["pvp-1"]["you"] == appearances["pvp-0"]["opponent"]
        clock.value = battles[0]["starts_at"]
        for index, page in enumerate(pages):
            refresh(page)
            expect(page.locator(".battle-tap")).to_be_enabled()
            check_frozen_battle(page, battles[index], f"pvp-{index}")
            assert_controls(page, f"pvp-{index}", top_inset=40, bottom_inset=30)
            save_view(page, artifacts, f"pvp-client-{index}")
        with pages[0].expect_response(lambda response: response.url.endswith("/api/v1/battle/tap")):
            pages[0].locator(".battle-tap").tap()
        refresh(pages[1])
        assert server_state(pages[1])["battle"]["opponent"]["taps"] == 1
        for index, page in enumerate(pages):
            assert battle_art(page, battles[index], f"pvp-{index} after tap") == appearances[f"pvp-{index}"]
        print("Passed: both PvP clients, snapshots, localization, polling, reload and tap", flush=True)
    finally:
        for context in contexts:
            context.close()
    return appearances


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT / "artifacts" / "rooster-art-review")
    parser.add_argument("--section", choices=("all", "arena", "battle"), default="all",
                        help="Run one section while investigating a failure; default checks everything.")
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    errors = []
    with TemporaryDirectory(prefix="roosters-art-") as temporary:
        app = create_app(Settings(app_env="test", allow_dev_auth=True, secret_key="test" * 16,
                                 database_path=str(Path(temporary) / "game.sqlite3")),
                         clock=clock, random_float=lambda: clock.draw)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
                browser = playwright.chromium.launch(headless=True, **({"executable_path": str(chrome)} if chrome.exists() else {}))
                try:
                    url = f"http://127.0.0.1:{server.server_port}"
                    appearances = {}
                    if args.section in ("all", "arena"):
                        appearances.update(exercise_arena(browser, url, app, args.artifacts_dir, errors))
                    if args.section in ("all", "battle"):
                        appearances.update(exercise_battles(browser, url, app, clock, args.artifacts_dir, errors))
                    assert not errors, errors
                    evidence = "appearances.json" if args.section == "all" else f"appearances-{args.section}.json"
                    (args.artifacts_dir / evidence).write_text(json.dumps(appearances, indent=2) + "\n", encoding="utf-8")
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print(f"Rooster art checks passed ({args.section}): live server appearances, RU/EN, themes, 320/360/390/430px and safe insets.")
    print(f"Screenshots and appearance evidence: {args.artifacts_dir}")


if __name__ == "__main__":
    main()
