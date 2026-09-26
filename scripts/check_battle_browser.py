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
    # Retaining the button also retains its 180ms battle-start feedback. Measure
    # resting geometry after it finishes, rather than a transient scale frame.
    page.wait_for_function("() => [...document.querySelector('.battle-tap').getAnimations()].every(a => a.playState !== 'running')")
    check_layout(page, label)
    expect(page.locator('#navigation')).to_be_hidden()
    expect(page.locator('.battle-help, .last-battle-card')).to_have_count(0)
    expect(page.locator('.battle-card.ui-panel')).to_have_count(1)
    expect(page.locator('.battle-tap.ui-button.ui-primary')).to_have_count(1)
    expect(page.locator('.battle-duration-track.ui-progress #battle-time-progress')).to_have_count(1)
    # Read one DOM snapshot: API updates replace markup, so separate locator
    # measurements can otherwise race with a detached node.
    geometry = page.evaluate("""() => {
        const button = document.querySelector('.battle-tap');
        const r = button.getBoundingClientRect();
        const overviewNode = document.querySelector('.battle-overview');
        const overview = overviewNode.getBoundingClientRect();
        const shell = document.querySelector('.app-shell');
        const style = getComputedStyle(shell);
        const usableHeight = shell.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) + 16;
        const essentials = ['.battle-heading', '.fighters', '#battle-odds', '#battle-prize']
          .map(selector => ({selector, node: document.querySelector(selector)}))
          .filter(({node}) => node.getClientRects().length)
          .map(({selector, node}) => ({selector, rect: node.getBoundingClientRect().toJSON()}));
        const caption = document.querySelector('#battle-caption');
        const captionBox = caption.getBoundingClientRect();
        const buttonStyle = getComputedStyle(button);
        const title = document.querySelector('.battle-heading h1');
        const titleBox = title.getBoundingClientRect();
        const titleRange = document.createRange();
        titleRange.selectNodeContents(title);
        const titleTextBox = titleRange.getBoundingClientRect();
        const timerBox = document.querySelector('.battle-timer').getBoundingClientRect();
        const languageBox = document.querySelector('.language-switch').getBoundingClientRect();
        const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
        return {button: r.toJSON(), overview: overview.toJSON(), essentials, usableHeight,
          scrollHeight: overviewNode.scrollHeight, scrollTop: overviewNode.scrollTop,
          sharedStyle: shell.classList.contains('ui-shell')
            && style.backgroundColor === 'rgb(7, 92, 219)'
            && getComputedStyle(document.querySelector('.battle-heading h1')).fontFamily.includes('Arena Nunito')
            && parseFloat(buttonStyle.borderTopWidth) >= 2
            && buttonStyle.backgroundImage !== 'none' && buttonStyle.opacity === '1',
          readableText: [...document.querySelectorAll('.battle-caption, .battle-tap > span, .fighter-meta, .battle-prize, .tap-score')]
            .every(node => parseFloat(getComputedStyle(node).fontSize) >= 14),
          captionReachable: caption.textContent.trim().length > 0 && captionBox.top >= r.bottom
            && captionBox.bottom <= shell.getBoundingClientRect().bottom - parseFloat(style.paddingBottom) + 1,
          headingClear: !overlaps(titleBox, timerBox) && !overlaps(titleBox, languageBox)
            && !overlaps(titleTextBox, timerBox) && !overlaps(titleTextBox, languageBox)
            && titleTextBox.width <= titleBox.width + 1
            && titleRange.getClientRects().length === 1
            && !overlaps(timerBox, languageBox) && timerBox.top >= parseFloat(style.paddingTop)
            && timerBox.bottom <= shell.getBoundingClientRect().bottom - parseFloat(style.paddingBottom),
          uncovered: [0.1, 0.5, 0.9].every(y => button.contains(document.elementFromPoint(
            r.x + r.width / 2, r.y + r.height * y)))};
    }""")
    box = geometry['button']
    assert box['height'] >= 44 and box['y'] >= top_inset, (label, box)
    assert abs(box['height'] - geometry['usableHeight'] / 2) <= 1, (label, geometry)
    assert box['bottom'] <= page.viewport_size['height'] - bottom_inset, (label, box)
    assert geometry['uncovered'], label + ': tap target covered'
    assert geometry['sharedStyle'], label + ': shared battle presentation missing'
    assert geometry['readableText'], label + ': important text smaller than 14px'
    assert geometry['captionReachable'], label + ': state caption clipped or missing'
    assert geometry['headingClear'], label + ': title, timer or language selector overlap'
    if essential:
        for item in geometry['essentials']:
            r = item['rect']
            # Details may scroll above the large tap target on short screens.
            content_bottom = r['bottom'] - geometry['overview']['top'] + geometry['scrollTop']
            assert content_bottom <= geometry['scrollHeight'] + 1, (label, item, geometry)


def exercise(browser, url, clock, artifacts):
    errors = []
    sizes = [(360, 640), (390, 844), (430, 932), (360, 560), (320, 568)]
    for width, height in sizes:
        context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='no-preference', has_touch=True)
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            status=200, content_type='application/javascript', body=BRIDGE))
        # Hold display time for repeatable screenshots; real API responses still
        # set the server offset. The main browser suite checks live deadlines.
        context.add_init_script('let snapshotTime = Date.now(); Date.now = () => snapshotTime; window.battleAdvanceTime = milliseconds => { snapshotTime += milliseconds; };')
        context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
            {'user_id': 'layout_%s' % width + '_%s' % height, 'name': 'Боец с очень длинным именем'}))
        context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
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
        # The half-screen tap target stays in place while fight details scroll.
        original_y = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().y")
        # Resolve, scroll and measure in one JS task: a poll can replace the
        # overview between locator resolution and a later element evaluation.
        scroll_metrics = '''scrollToBottom => {
            const node = document.querySelector('.battle-overview');
            if (scrollToBottom) node.scrollTop = node.scrollHeight;
            return {
                scrollTop: node.scrollTop, scrollHeight: node.scrollHeight, clientHeight: node.clientHeight,
                rect: node.getBoundingClientRect().toJSON(), connected: node.isConnected,
                viewport: {width: innerWidth, height: innerHeight},
                language: document.documentElement.lang,
                names: [...node.querySelectorAll('.fighter-name')].map(n => n.textContent)
            };
        }'''
        scroll_before = page.evaluate(scroll_metrics, True)
        refresh(page)
        scroll_after = page.evaluate(scroll_metrics, False)
        assert scroll_after['scrollTop'] == scroll_before['scrollTop'], {
            'viewport': f'{width}x{height}', 'before': scroll_before, 'after': scroll_after,
        }
        assert_controls(page, 'scrolled details', essential=False)
        # Read position in one JS task: polling can detach a locator between
        # Playwright's element resolution and its separate bounding-box request.
        current_y = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().y")
        assert abs(current_y - original_y) < 1
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
        # Keep a real touch held across state rendering. Replacing the button
        # between touchstart and touchend used to lose this input on phones.
        page.evaluate("window.savedTap = document.querySelector('.battle-tap')")
        box = page.locator('.battle-tap').bounding_box()
        touch = context.new_cdp_session(page)
        touch.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [
            {'x': box['x'] + box['width'] / 2, 'y': box['y'] + box['height'] / 2}]})
        refresh(page)
        page.wait_for_load_state('networkidle')
        assert page.evaluate("savedTap === document.querySelector('.battle-tap') && savedTap.isConnected"), 'poll detached tap control'
        with page.expect_response(lambda response: response.url.endswith('/api/v1/battle/tap')) as held:
            touch.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        assert held.value.json()['state']['battle']['you']['taps'] == 1
        touch.detach()
        # Real taps and server totals, including a lost tap response and retry.
        def lost_tap(route):
            route.fetch()
            route.abort('failed')
        context.route('**/api/v1/battle/tap', lost_tap, times=1)
        with page.expect_response(lambda response: response.url.endswith('/api/v1/battle/tap')) as recovered:
            page.locator('.battle-tap').tap()
            expect(page.locator('[data-action=retry]')).to_be_visible()
            expect(page.locator('.battle-tap')).to_be_enabled()
            page.locator('.battle-tap').tap()
            assert_controls(page, 'tap retry')
            page.screenshot(path=str(artifacts / f'battle-{width}x{height}-retry.png'))
        assert recovered.value.json()['state']['battle']['you']['taps'] == 2
        expect(page.locator('.tap-score strong').first).to_have_text('3 / 90')
        assert server_state(page)['battle']['you']['taps'] == 3
        # Keyboard support uses the same command and must not add a second
        # native click when Space is released on the focused tap button.
        page.locator('.battle-tap').focus()
        with page.expect_response(lambda response: response.url.endswith('/api/v1/battle/tap')) as keyed:
            page.keyboard.press('Space')
        assert keyed.value.json()['state']['battle']['you']['taps'] == 4
        with page.expect_response(lambda response: response.url.endswith('/api/v1/battle/tap')) as tapped:
            page.evaluate("() => { for(let i = 0; i < 86; i++) document.querySelector('.battle-tap').click(); }")
        assert tapped.value.json()['state']['battle']['you']['taps'] == 90
        expect(page.locator('.battle-tap')).to_be_disabled()
        assert_controls(page, 'maxed')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-maxed.png'))
        assert server_state(page)['player']['balance_minor'] == initial['player']['balance_minor']
        # Let the display clock reach the deadline before the next real server
        # settlement. The waiting state keeps navigation hidden and taps locked.
        clock.value = battle['ends_at'] - .1
        refresh(page)
        expect(page.locator('#battle-seconds')).to_have_text('1')
        # Drain in-flight presence/state responses before advancing local time:
        # each authoritative response correctly resets the display offset.
        page.wait_for_load_state('networkidle')
        held_updates = []
        for endpoint in ('state', 'presence'):
            page.route('**/api/v1/' + endpoint, lambda route: held_updates.append(route))
        # Run the display clock normally for this transition instead of freezing
        # it at a fractional deadline while a final response is being rendered.
        page.evaluate('window.battleWaitingClock = setInterval(() => battleAdvanceTime(250), 250)')
        expect(page.locator('#battle-seconds')).to_have_text('0')
        page.evaluate('clearInterval(battleWaitingClock)')
        expect(page.locator('#battle-caption')).to_have_text('Battle finished. Waiting for the server result…')
        assert_controls(page, 'waiting for result')
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-waiting.png'))
        clock.value = battle['ends_at'] + 1
        for route in held_updates:
            route.continue_()
        for endpoint in ('state', 'presence'):
            page.unroute('**/api/v1/' + endpoint)
        page.wait_for_load_state('networkidle')
        refresh(page)
        expect(page.locator('#navigation')).to_be_visible()
        expect(page.locator('.battle-layout')).to_have_count(0)
        expect(page.locator('.last-battle-card, #last-result')).to_have_count(0)
        expect(page.locator('.history-row')).not_to_have_count(0)
        expect(page.locator('#battle-result-toast.ui-result')).to_be_visible()
        page.screenshot(path=str(artifacts / f'battle-{width}x{height}-result.png'))
        page.locator('[data-tab=gear]').click()
        expect(page.locator('.equipment-grid')).to_be_visible()
        # A prior completed fight must not reappear during the next battle.
        page.locator('[data-tab=arena]').click()
        if page.locator('[data-action=close-result]').is_visible():
            page.locator('[data-action=close-result]').click()
        command(page, '[data-action=start-bot]', 'battle/start')
        assert_controls(page, 'second battle with history')
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
    print('Battle browser checks passed: 320/360/390/430px, short height, RU/EN, dark/light, shared styles, readable captions, safe areas, reload, scroll, retry, Space, tap cap, waiting state, navigation restoration.')
    print(f'Screenshots: {args.artifacts_dir}')


if __name__ == '__main__':
    main()
