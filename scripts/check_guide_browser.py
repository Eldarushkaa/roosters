"""First-launch guide and accessible manual help on a disposable real backend.

Run: python -m scripts.check_guide_browser. Telegram insets are simulated.
"""
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, server_state
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent
SEEN_KEY = 'rooster.v1.guideSeen.v1'
SIZES = [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]


def seen(page):
    return page.evaluate('key => localStorage.getItem(key)', SEEN_KEY)


def stable_guide_scroll(page):
    """Wait for native PageDown/wheel motion before testing a new scroll state."""
    return page.evaluate('''() => new Promise((resolve, reject) => {
        let previous = null, stableFrames = 0, finished = false;
        const timer = setTimeout(() => {
            finished = true;
            reject(Error('Guide scroll did not settle within 2 seconds'));
        }, 2000);
        const sample = () => {
            if (finished) return;
            const position = document.querySelector('.guide-content').scrollTop;
            stableFrames = previous !== null && Math.abs(position - previous) < .1 ? stableFrames + 1 : 0;
            previous = position;
            if (stableFrames >= 5) {
                finished = true;
                clearTimeout(timer);
                resolve(position);
            } else requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
    })''')


def assert_guide_scroll(page, expected, label):
    actual = stable_guide_scroll(page)
    maximum = page.locator('.guide-content').evaluate('node => Math.max(0, node.scrollHeight - node.clientHeight)')
    assert abs(actual - min(expected, maximum)) < 1, (label, expected, actual, maximum)
    return actual


def guide_geometry(page, label):
    check_layout(page, label)
    expect(page.locator('#guide-overlay')).to_be_visible()
    expect(page.locator('#quick-guide')).to_have_attribute('role', 'dialog')
    expect(page.locator('#quick-guide')).to_have_attribute('aria-modal', 'true')
    expect(page.locator('#quick-guide')).to_have_attribute('aria-labelledby', 'guide-title')
    expect(page.locator('#guide-title')).not_to_be_empty()
    expect(page.locator('.guide-steps > li')).to_have_count(6)
    cards = page.locator('.guide-steps > li')
    for index in (0, 2, 4):
        left, right = cards.nth(index).bounding_box(), cards.nth(index + 1).bounding_box()
        assert abs(left['y'] - right['y']) < 1, (label, left, right)
        assert left['x'] + left['width'] <= right['x'], (label, left, right)
    page.wait_for_function('''() => [...document.querySelectorAll('#quick-guide img')]
        .every(image => image.complete && image.naturalWidth > 0)''')
    if page.locator('html').get_attribute('lang') == 'en':
        names = page.locator('#quick-guide [aria-label]').evaluate_all('nodes => nodes.map(n => n.getAttribute("aria-label")).join(" ")')
        assert not re.search(r'[А-Яа-яЁё]', page.locator('#quick-guide').inner_text() + names), label
    geometry = page.evaluate('''() => {
        const dialog = document.querySelector('#quick-guide');
        const body = dialog.querySelector('.guide-content');
        const close = [...dialog.querySelectorAll('[data-action=close-guide]')].map(node => {
            const r = node.getBoundingClientRect();
            return {rect:r.toJSON(), reachable:node.contains(document.elementFromPoint(
                r.x + r.width / 2, r.y + r.height / 2))};
        });
        return {rect:dialog.getBoundingClientRect().toJSON(), close,
            body:{rect:body.getBoundingClientRect().toJSON(), scrollTop:body.scrollTop,
                clientHeight:body.clientHeight,scrollHeight:body.scrollHeight,
                clientWidth:body.clientWidth,scrollWidth:body.scrollWidth},
            cards:[...dialog.querySelectorAll('.guide-card')].map(n => n.getBoundingClientRect().toJSON()),
            focusInside:dialog.contains(document.activeElement),
            backgroundInert:document.querySelector('.app-shell').inert && document.querySelector('#navigation').inert,
            targets:[...dialog.querySelectorAll('button')].map(n => ({
                text:n.getAttribute('aria-label') || n.textContent,
                rect:n.getBoundingClientRect().toJSON()}))};
    }''')
    box = geometry['rect']
    assert box['top'] >= 40 and box['bottom'] <= page.viewport_size['height'] - 30, (label, geometry)
    assert box['left'] >= 6 and box['right'] <= page.viewport_size['width'] - 8, (label, geometry)
    assert geometry['backgroundInert'] and geometry['focusInside'], (label, geometry)
    assert geometry['body']['scrollWidth'] <= geometry['body']['clientWidth'] + 1, (label, geometry)
    if page.viewport_size['width'] < 600 and geometry['body']['scrollTop'] == 0:
        for card in geometry['cards'][:4]:
            assert card['top'] >= geometry['body']['rect']['top'] - 1, (label, geometry)
            assert card['bottom'] <= geometry['body']['rect']['bottom'] + 1, (label, geometry)
        heights = [card['height'] for card in geometry['cards']]
        assert max(heights) - min(heights) < 1, (label, heights)
    assert len(geometry['close']) == 2, (label, geometry)
    for target in geometry['targets']:
        assert target['text'].strip(), (label, target)
        assert target['rect']['height'] >= 44 and target['rect']['width'] >= 44, (label, target)
    for target in geometry['close']:
        assert target['reachable'] and target['rect']['top'] >= 40, (label, geometry)
        assert target['rect']['bottom'] <= page.viewport_size['height'] - 30, (label, geometry)
    return geometry


def check_focus_trap(page):
    controls = page.locator('#quick-guide button:visible')
    controls.first.focus()
    page.keyboard.press('Shift+Tab')
    expect(controls.last).to_be_focused()
    page.keyboard.press('Tab')
    expect(controls.first).to_be_focused()
    for _ in range(controls.count() + 1):
        page.keyboard.press('Tab')
        assert page.evaluate('document.querySelector("#quick-guide").contains(document.activeElement)')


def assert_closed(page):
    expect(page.locator('#guide-overlay')).to_be_hidden()
    assert seen(page) == '1'
    assert page.locator('#quick-guide [data-language]').count() == 0
    assert page.evaluate('!document.querySelector(".app-shell").inert && !document.querySelector("#navigation").inert')


def exercise(browser, url, clock, artifacts):
    errors, matrix = [], []

    def context_for(identity, width=390, height=844, language='ru', theme='dark'):
        context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=theme,
                                      reduced_motion='reduce')
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(theme))))
        context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
        context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
        context.add_init_script("""if (!localStorage.getItem('rooster.v1.identity')) {
            localStorage.setItem('rooster.v1.identity', JSON.stringify(%s));
        }""" % json.dumps({'user_id': identity, 'name': 'Guide ' + identity}))
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        mutations = []

        def record(request):
            if (request.method == 'POST' and '/api/v1/' in request.url
                    and '/auth/' not in request.url and not request.url.endswith('/presence')):
                mutations.append(request.url)

        page.on('request', record)
        page.goto(url, wait_until='networkidle')
        return context, page, mutations

    for width, height in SIZES:
        for language in ('ru', 'en'):
            for theme in ('light', 'dark'):
                label = f'{width}x{height}-{language}-{theme}'
                context, page, mutations = context_for('guide_' + label, width, height, language, theme)
                try:
                    expect(page.locator('#guide-overlay')).to_be_visible()
                    assert seen(page) is None
                    balance = server_state(page)['player']['balance_minor']
                    expect(page.locator('html')).to_have_attribute('lang', language)
                    geometry = guide_geometry(page, label)
                    assert geometry['body']['scrollTop'] == 0, (label, geometry)
                    page.screenshot(path=str(artifacts / (label + '-first.png')))
                    check_focus_trap(page)
                    if geometry['body']['scrollHeight'] > geometry['body']['clientHeight']:
                        page.locator('[data-focus=guide-close]').focus()
                        page.keyboard.press('Tab')
                        expect(page.locator('.guide-content')).to_be_focused()
                        page.keyboard.press('PageDown')
                        page.wait_for_function('() => document.querySelector(".guide-content").scrollTop > 0')
                        assert stable_guide_scroll(page) > 0, label
                        page.locator('.guide-content').evaluate('node => node.scrollTop = 0')
                        assert_guide_scroll(page, 0, label + '-keyboard-reset')
                    # Merely reading or reloading the guide cannot mark it seen.
                    if label == '320x568-ru-light':
                        page.reload(wait_until='networkidle')
                        expect(page.locator('#guide-overlay')).to_be_visible()
                        assert seen(page) is None
                    page.locator('.guide-content').evaluate('node => node.scrollTop = Math.min(80, node.scrollHeight - node.clientHeight)')
                    preserved_scroll = stable_guide_scroll(page)
                    refresh(page)
                    page.wait_for_load_state('networkidle')
                    preserved_scroll = assert_guide_scroll(page, preserved_scroll, label + '-poll-scroll')
                    alternate = 'en' if language == 'ru' else 'ru'
                    page.locator(f'#quick-guide [data-language={alternate}]').click()
                    expect(page.locator('html')).to_have_attribute('lang', alternate)
                    expect(page.locator('#guide-title')).not_to_be_empty()
                    assert seen(page) is None
                    preserved_scroll = assert_guide_scroll(page, preserved_scroll, label + '-alternate-language-scroll')
                    page.locator(f'#quick-guide [data-language={language}]').click()
                    expect(page.locator('html')).to_have_attribute('lang', language)
                    assert_guide_scroll(page, preserved_scroll, label + '-original-language-scroll')
                    locked_scroll = page.evaluate('scrollY')
                    page.mouse.move(2, height / 2)
                    page.mouse.wheel(0, 350)
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    assert page.evaluate('scrollY') == locked_scroll
                    page.locator('.guide-content').evaluate('node => node.scrollTop = node.scrollHeight')
                    scrolled = guide_geometry(page, label + '-scrolled')
                    if scrolled['body']['scrollHeight'] > scrolled['body']['clientHeight']:
                        assert scrolled['body']['scrollTop'] > 0, (label, scrolled)
                    page.screenshot(path=str(artifacts / (label + '-scrolled.png')))
                    page.locator('[data-focus=guide-close]').click()
                    assert_closed(page)
                    expect(page.locator('#open-guide')).to_be_focused()
                    expect(page.locator('#open-guide')).to_be_visible()
                    header = page.locator('#open-guide').bounding_box()
                    assert header['width'] >= 44 and header['height'] >= 44, (label, header)
                    assert page.locator('#open-guide .ui-icon').count() == 1
                    page.screenshot(path=str(artifacts / (label + '-returned-header.png')))
                    page.reload(wait_until='networkidle')
                    expect(page.locator('#guide-overlay')).to_be_hidden()
                    page.locator('#open-guide').click()
                    guide_geometry(page, label + '-manual')
                    page.keyboard.press('Escape')
                    assert_closed(page)
                    expect(page.locator('#open-guide')).to_be_focused()
                    # Preserve the underlying page position across a modal cycle.
                    page.evaluate('scrollTo(0, 160)')
                    before_scroll = page.evaluate('scrollY')
                    page.locator('#open-guide').evaluate('button => button.click()')
                    expect(page.locator('#guide-overlay')).to_be_visible()
                    page.locator('#quick-guide [data-action=close-guide]').last.click()
                    assert_closed(page)
                    assert abs(page.evaluate('scrollY') - before_scroll) < 1, label
                    assert server_state(page)['player']['balance_minor'] == balance
                    assert not mutations, (label, mutations)
                    matrix.append({'label': label, 'initial': geometry, 'scrolled': scrolled})
                finally:
                    context.close()

    # The same two-column composition also scales to wider viewports.
    for width, height in [(680, 960), (1024, 1536)]:
        for language in ('ru', 'en'):
            label = f'{width}x{height}-{language}-desktop'
            context, page, mutations = context_for('guide_' + label, width, height, language)
            try:
                guide_geometry(page, label)
                cards = page.locator('.guide-steps > li')
                assert abs(cards.nth(0).bounding_box()['y'] - cards.nth(1).bounding_box()['y']) < 1
                page.screenshot(path=str(artifacts / (label + '.png')))
                assert not mutations, (label, mutations)
            finally:
                context.close()

    # An interrupted first introduction must not cover an active battle. The
    # unseen guide waits until settlement and explicit result-toast dismissal.
    context, page, _ = context_for('guide_restored_battle')
    try:
        page.locator('[data-focus=guide-close]').click()
        battle = command(page, '[data-action=start-free]', 'battle/start')['state']['battle']
        page.evaluate('key => localStorage.removeItem(key)', SEEN_KEY)
        page.reload(wait_until='networkidle')
        expect(page.locator('#guide-overlay')).to_be_hidden()
        expect(page.locator('#open-guide')).to_be_hidden()
        expect(page.locator('.battle-card')).to_be_visible()
        assert seen(page) is None
        assert server_state(page)['battle']['id'] == battle['id']
        clock.value = battle['ends_at'] + 1
        refresh(page)
        expect(page.locator('#battle-result-toast')).to_be_visible()
        expect(page.locator('#guide-overlay')).to_be_hidden()
        page.locator('[data-action=close-result]').click()
        expect(page.locator('#guide-overlay')).to_be_visible()
        guide_geometry(page, 'restored-battle-finished')
        page.screenshot(path=str(artifacts / 'restored-battle-finished.png'))
        page.keyboard.press('Escape')
        assert_closed(page)
    finally:
        context.close()

    # A restored queue defers automatic help, but manual reading does not stop
    # the search. Leaving that unseen queue makes automatic help available.
    context, page, mutations = context_for('guide_restored_queue')
    try:
        page.locator('[data-focus=guide-close]').click()
        queued = command(page, '[data-action=queue-join]', 'queue/join')['state']
        page.evaluate('key => localStorage.removeItem(key)', SEEN_KEY)
        page.reload(wait_until='networkidle')
        expect(page.locator('#guide-overlay')).to_be_hidden()
        expect(page.locator('#open-guide')).to_be_visible()
        expect(page.locator('.queue-card')).to_be_visible()
        assert seen(page) is None
        mutations.clear()
        page.locator('#open-guide').click()
        guide_geometry(page, 'queue-manual')
        page.keyboard.press('Escape')
        assert_closed(page)
        assert server_state(page)['queue'] == queued['queue']
        assert not mutations, mutations
        page.evaluate('key => localStorage.removeItem(key)', SEEN_KEY)
        page.reload(wait_until='networkidle')
        expect(page.locator('#guide-overlay')).to_be_hidden()
        command(page, '[data-action=queue-leave]', 'queue/leave')
        expect(page.locator('#guide-overlay')).to_be_visible()
        assert seen(page) is None
        page.locator('[data-focus=guide-close]').click()
        assert_closed(page)
        # Seen belongs to this browser, including a subsequent account login.
        page.evaluate("""() => {
            localStorage.setItem('rooster.v1.identity', JSON.stringify({user_id:'guide_other_account',name:'Other account'}));
            sessionStorage.clear();
        }""")
        page.reload(wait_until='networkidle')
        expect(page.locator('#guide-overlay')).to_be_hidden()
        assert server_state(page)['player']['id'] == 'dev:guide_other_account'
        assert seen(page) == '1'
    finally:
        context.close()
    assert not errors, errors
    (artifacts / 'matrix.json').write_text(json.dumps(matrix, indent=2))
    print(f'Guide browser passed: {len(SIZES) * 4} mobile combinations and 4 desktop layouts, six illustrated steps, first launch, persistence, manual open, focus/inert/scroll, localization, no commands and restored battle/queue deferral.')


def main():
    artifacts = ROOT / 'artifacts' / 'guide-review'
    artifacts.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-guide-') as temp:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='test' * 16,
            database_path=str(Path(temp) / 'game.sqlite3')), clock=clock, random_float=lambda: clock.draw)
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = playwright.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
                try:
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', clock, artifacts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


if __name__ == '__main__':
    main()
