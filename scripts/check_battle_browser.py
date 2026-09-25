"""Battle-only viewport checks against the real API and a disposable database.

Run: python -m scripts.check_battle_browser
Telegram bridge values are simulated; native Telegram gestures need device QA.
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
from scripts.check_browser import Clock, QuietRequests, command, refresh, server_state, check_layout

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = """
const callbacks = {};
window.Telegram = {WebApp: {
  colorScheme: 'dark', ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {},
  onEvent(name, callback) { callbacks[name] = callback; }
}};
window.battleTheme = scheme => {
  Telegram.WebApp.colorScheme = scheme;
  callbacks.themeChanged?.();
};
"""


def assert_controls(page, label, essential=True, bottom_inset=0, top_inset=0):
    check_layout(page, label)
    expect(page.locator('#navigation')).to_be_hidden()
    # Read one DOM snapshot: API updates replace markup, so separate locator
    # measurements can otherwise race with a detached node.
    geometry = page.evaluate("""() => {
        const button = document.querySelector('.battle-tap');
        const r = button.getBoundingClientRect();
        const overview = document.querySelector('.battle-overview').getBoundingClientRect();
        const essentials = ['.battle-heading', '.fighters', '#battle-odds', '#battle-prize']
          .map(selector => ({selector, node: document.querySelector(selector)}))
          .filter(({node}) => node.getClientRects().length)
          .map(({selector, node}) => ({selector, rect: node.getBoundingClientRect().toJSON()}));
        return {button: r.toJSON(), overview: overview.toJSON(), essentials,
          uncovered: [0.1, 0.5, 0.9].every(y => button.contains(document.elementFromPoint(
            r.x + r.width / 2, r.y + r.height * y)))};
    }""")
    box = geometry['button']
    assert box['height'] >= 44 and box['y'] >= top_inset, (label, box)
    assert box['bottom'] <= page.viewport_size['height'] - bottom_inset, (label, box)
    assert geometry['uncovered'], label + ': tap target covered'
    if essential:
        for item in geometry['essentials']:
            r = item['rect']
            assert r['top'] >= top_inset and r['bottom'] <= geometry['overview']['bottom'] + 1, (label, item, geometry)


def exercise(browser, url, clock, artifacts):
    errors = []
    sizes = [(360, 640), (390, 844), (430, 932), (360, 560), (320, 568)]
    for width, height in sizes:
        context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='no-preference')
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            status=200, content_type='application/javascript', body=BRIDGE))
        # Hold display time for repeatable screenshots; real API responses still
        # set the server offset. The main browser suite checks live deadlines.
        context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
        context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
            {'user_id': 'layout_%s' % width + '_%s' % height, 'name': 'Боец с очень длинным именем'}))
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        initial = server_state(page)
        started = command(page, '[data-action="start-free"]', 'battle/start')
        battle = started['state']['battle']
        expect(page.locator('.battle-tap')).to_be_disabled()
        assert_controls(page, f'{width}x{height} preparing')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-preparing.png'))
        clock.value = battle['starts_at']
        refresh(page)
        expect(page.locator('.battle-tap')).to_be_enabled()
        for language in ('ru', 'en'):
            page.locator(f'[data-language="{language}"]').click()
            refresh(page)
            assert_controls(page, f'{width}x{height} {language}')
            page.screenshot(path=str(artifacts / f'battle-{width}x{height}-{language}.png'))
        # The grid's bottom row stays in place while the help and history scroll.
        original_y = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().y")
        page.locator('.battle-help summary').click()
        page.locator('.battle-overview').evaluate('node => node.scrollTop = node.scrollHeight')
        scroll_top = page.locator('.battle-overview').evaluate('node => node.scrollTop')
        refresh(page)
        expect(page.locator('.battle-help')).to_have_attribute('open', '')
        assert page.locator('.battle-overview').evaluate('node => node.scrollTop') == scroll_top
        assert_controls(page, 'scrolled help', essential=False)
        # Read position in one JS task: polling can detach a locator between
        # Playwright's element resolution and its separate bounding-box request.
        current_y = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().y")
        assert abs(current_y - original_y) < 1
        page.locator('.battle-help').evaluate('node => node.open = false')
        page.locator('.battle-overview').evaluate('node => node.scrollTop = 0')
        # A theme event must not rebuild fighters or change server values.
        page.evaluate("window.savedFighter = document.querySelector('[data-fighter=you]'); battleTheme('light')")
        assert page.evaluate("savedFighter === document.querySelector('[data-fighter=you]')")
        assert_controls(page, 'light')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-light.png'))
        page.evaluate("battleTheme('dark')")
        # Reloading an active battle from a stale destination returns to gameplay.
        page.evaluate("history.replaceState(null, '', '#gear')")
        page.reload(wait_until='networkidle')
        assert page.url.endswith('#arena')
        assert server_state(page)['battle']['id'] == battle['id']
        refresh(page)
        assert_controls(page, 'reload')
        # Dynamic Telegram CSS variables, including a stable viewport smaller
        # than the browser window and both device and content safe areas.
        if width == 390:
            page.evaluate('''() => {
                const s = document.documentElement.style;
                s.setProperty('--tg-viewport-stable-height', '640px');
                s.setProperty('--tg-safe-area-inset-top', '24px');
                s.setProperty('--tg-content-safe-area-inset-top', '20px');
                s.setProperty('--tg-safe-area-inset-bottom', '24px');
                s.setProperty('--tg-content-safe-area-inset-bottom', '10px');
                s.setProperty('--tg-safe-area-inset-left', '4px');
                s.setProperty('--tg-content-safe-area-inset-right', '8px');
            }''')
            assert_controls(page, 'Telegram collapsed', essential=False, top_inset=44)
            controls = page.locator('.battle-controls').bounding_box()
            assert controls['y'] + controls['height'] <= 640 - 34
            assert page.locator('.battle-layout').bounding_box()['x'] >= 20
            page.screenshot(path=str(artifacts / 'battle-telegram-safe-areas.png'))
            page.evaluate("document.documentElement.removeAttribute('style')")
        # Real taps and server totals, including a lost tap response and retry.
        def lost_tap(route):
            route.fetch()
            route.abort('failed')
        context.route('**/api/v1/battle/tap', lost_tap, times=1)
        page.locator('.battle-tap').click()
        expect(page.locator('[data-action=retry]')).to_be_visible()
        assert_controls(page, 'tap retry')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-retry.png'))
        command(page, '[data-action=retry]', 'battle/tap')
        assert server_state(page)['battle']['you']['taps'] == 1
        with page.expect_response(lambda response: response.url.endswith('/api/v1/battle/tap')) as tapped:
            page.evaluate("() => { for(let i = 0; i < 89; i++) document.querySelector('.battle-tap').click(); }")
        assert tapped.value.json()['state']['battle']['you']['taps'] == 90
        expect(page.locator('.battle-tap')).to_be_disabled()
        assert_controls(page, 'maxed')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-maxed.png'))
        assert server_state(page)['player']['balance_minor'] == initial['player']['balance_minor']
        clock.value = battle['ends_at'] + 1
        refresh(page)
        expect(page.locator('#navigation')).to_be_visible()
        expect(page.locator('.battle-layout')).to_have_count(0)
        page.locator('[data-tab=gear]').click()
        expect(page.locator('.equipment-grid')).to_be_visible()
        context.close()
    assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=ROOT / 'artifacts' / 'battle-layout')
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-battle-layout-') as temporary:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='test' * 16,
                                 database_path=str(Path(temporary) / 'game.sqlite3')),
                         clock=clock, random_float=lambda: clock.draw)
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = playwright.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
                try:
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', clock, args.artifacts_dir)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Battle browser checks passed: 320/360/390/430px, short height, RU/EN, dark/light, safe areas, reload, scroll, retry, tap cap, navigation restoration.')
    print(f'Screenshots: {args.artifacts_dir}')


if __name__ == '__main__':
    main()
