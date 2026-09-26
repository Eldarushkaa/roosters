"""Online result/history checks against a disposable API and simulated Telegram."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, refresh, server_state, check_layout
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent


def exercise(browser, url, clock, service, artifacts):
    errors = []
    for width, height in [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]:
        for theme in ("light", "dark"):
            context = browser.new_context(viewport={"width": width, "height": height},
                                          reduced_motion="reduce", has_touch=True)
            context.route("https://telegram.org/js/telegram-web-app.js", lambda route: route.fulfill(
                status=200, content_type="application/javascript", body=BRIDGE.replace("SCHEME", json.dumps(theme))))
            context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
            identity = {"user_id": f"result_{width}_{height}_{theme}", "name": "Боец с длинным именем"}
            context.add_init_script("localStorage.setItem('rooster.v1.identity', %s)" % json.dumps(json.dumps(identity)))
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="networkidle")
            page.evaluate("theme => { Telegram.WebApp.colorScheme = theme; shellEmit('themeChanged'); }", theme)
            mine = server_state(page)["player"]["id"]
            opponent = mine + "_other"
            service.register(opponent, "Соперник с длинным именем", True)
            service.command(mine, str(uuid4()), "queue/join", {"stake_minor": 1000})
            service.command(opponent, str(uuid4()), "queue/join", {"stake_minor": 2500})
            battle = service.state(mine)["battle"]
            clock.value = battle["starts_at"]
            service.command(mine, str(uuid4()), "battle/tap", {"battle_id": battle["id"], "taps": 90})
            service.command(opponent, str(uuid4()), "battle/tap", {"battle_id": battle["id"], "taps": 30})
            clock.value = battle["ends_at"]
            service.tick()
            refresh(page)
            toast = page.locator("#battle-result-toast")
            expect(toast).to_be_visible()
            expect(toast.locator(".result-analysis tbody tr")).to_have_count(2)
            expect(toast.locator("tbody tr").nth(0).locator("td").first).to_have_text("90")
            expect(toast.locator("tbody tr").nth(1).locator("td").first).to_have_text("30")
            for language in ("ru", "en"):
                page.locator(f'[data-language="{language}"]').click()
                expect(toast).to_contain_text("52,94%" if language == "ru" else "52.94%")
                expect(toast).to_contain_text("47,06%" if language == "ru" else "47.06%")
                check_layout(page, f"{width}-{height}-{theme}-{language}")
                geometry = toast.evaluate("""node => {
                    const rect = node.getBoundingClientRect();
                    const close = node.querySelector('[data-action="close-result"]');
                    const button = close.getBoundingClientRect();
                    return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
                        overflow: node.scrollWidth > node.clientWidth + 1,
                        closeSize: Math.min(button.width, button.height),
                        closeReachable: close.contains(document.elementFromPoint(button.x + button.width/2, button.y + button.height/2))};
                }""")
                assert 0 <= geometry["left"] < geometry["right"] <= width
                assert geometry["top"] >= 24 and geometry["bottom"] <= height - 20
                assert not geometry["overflow"] and geometry["closeSize"] >= 44 and geometry["closeReachable"], geometry
                page.screenshot(path=str(artifacts / f"online-{width}x{height}-{theme}-{language}.png"))
                # Polls preserve the result DOM and reading position.
                toast.evaluate("node => { window.savedResult = node.firstElementChild; node.scrollTop = 20; window.savedScroll = node.scrollTop; }")
                refresh(page)
                assert toast.evaluate("node => node.firstElementChild === window.savedResult && node.scrollTop === window.savedScroll")
            page.wait_for_timeout(4200)
            expect(toast).to_be_visible()
            toast.locator('[data-action="close-result"]').click()
            expect(toast).to_be_hidden()
            row = page.locator('[data-action="open-result"]').first
            row.scroll_into_view_if_needed()
            row.focus()
            page.keyboard.press("Enter")
            expect(toast).to_be_visible()
            expect(toast.locator('[data-action="close-result"]')).to_be_focused()
            page.keyboard.press("Escape")
            expect(toast).to_be_hidden()
            expect(row).to_be_focused()
            page.reload(wait_until="networkidle")
            expect(toast).to_be_hidden()
            page.locator('[data-action="open-result"]').first.click()
            expect(toast.locator(".result-analysis")).to_be_visible()
            context.close()
    assert not errors, errors


def main():
    artifacts = ROOT / "artifacts" / "online-result-review"
    artifacts.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix="roosters-online-result-") as temporary:
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
                    exercise(browser, f"http://127.0.0.1:{server.server_port}", clock,
                             app.extensions["game"], artifacts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print("Online result/history browser checks passed: 20 views, 320–430px, RU/EN, light/dark, safe areas, polling, focus, reload.")


if __name__ == "__main__":
    main()
