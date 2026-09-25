"""Cross-screen UX checks, real temporary backend and simulated Telegram bridge.

Complements the per-screen state matrices; no real accounts or database are used.
Run: python -m scripts.check_consistency_browser
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
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, tab
from scripts.check_shell_browser import BRIDGE, nav_geometry
from scripts.check_battle_browser import assert_controls

ROOT = Path(__file__).resolve().parent.parent
SIZES = [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]


def shared_controls(page, label):
    check_layout(page, label)
    assert '\ufffd' not in page.locator('body').inner_text(), label + '-replacement-glyph'
    assert page.locator('.language-switch button, #navigation button').evaluate_all('''nodes =>
        nodes.filter(n => n.getClientRects().length).every(n => {
            const r = n.getBoundingClientRect();
            return r.height >= 44 && r.width >= 44;
        })'''), label
    assert page.locator('svg.ui-icon').evaluate_all('''nodes => nodes.every(node =>
        (node.hasAttribute('aria-hidden') || node.hasAttribute('aria-label'))
        && node.querySelector('use')?.getAttribute('href')?.includes('/icons.svg#'))'''), label + '-shared-icons'
    assert page.evaluate('''async () => {
        const uses = [...document.querySelectorAll('svg.ui-icon use')];
        const urls = [...new Set(uses.map(use => use.getAttribute('href').split('#')[0]))];
        const sprites = new Map(await Promise.all(urls.map(async url => {
            const response = await fetch(url);
            if (!response.ok) return [url, null];
            return [url, new DOMParser().parseFromString(await response.text(), 'image/svg+xml')];
        })));
        return uses.every(use => {
            const [url, id] = use.getAttribute('href').split('#');
            return sprites.get(url)?.getElementById(id)?.localName === 'symbol';
        });
    }'''), label + '-sprite-symbols'
    assert page.locator('#navigation button > span:last-child').evaluate_all('''nodes =>
        nodes.every(n => {
            const text = n.getBoundingClientRect(), button = n.parentElement.getBoundingClientRect();
            return parseFloat(getComputedStyle(n).fontSize) >= 12
                && text.left >= button.left && text.right <= button.right;
        })'''), label


def illustrated_scope(page, destination, label):
    """Shared shell stays blue in every state, including native theme events."""
    assert page.locator('.app-shell.ui-shell').count() == 1, label
    assert page.locator('html.ui-game-screen').count() == 1, label
    assert page.locator('.app-shell').evaluate('''node => {
        const css = getComputedStyle(node);
        return css.backgroundColor === 'rgb(7, 92, 219)'
            && css.color === 'rgb(255, 255, 255)';
    }'''), label + '-shell-palette'
    standard_screen = destination in ['gear', 'roost', 'leaderboard']
    assert page.locator('#main .ui-screen').count() == int(standard_screen), label
    if standard_screen:
        assert page.locator('.ui-screen').evaluate('''node => {
            const css = getComputedStyle(node);
            const heading = getComputedStyle(node.querySelector('h1'));
            return css.backgroundColor === 'rgb(7, 92, 219)'
                && css.color === 'rgb(255, 255, 255)'
                && heading.fontFamily.includes('Arena Nunito');
        }'''), label
        assert page.evaluate('''async () => {
            const {contrast} = await import('/static/environment.mjs');
            const screen = document.querySelector('.ui-screen');
            const probe = document.createElement('span');
            probe.className = 'ui-muted'; screen.append(probe);
            const hex = rgb => '#' + rgb.match(/\\d+/g).slice(0, 3)
                .map(n => Number(n).toString(16).padStart(2, '0')).join('');
            const ratio = contrast(hex(getComputedStyle(probe).color),
                hex(getComputedStyle(screen).backgroundColor));
            probe.remove();
            return ratio >= 4.5;
        }'''), label + '-muted-text-contrast'


def exercise(browser, url, clock, artifacts):
    errors = []
    for width, height in SIZES:
        for scheme in ['light', 'dark']:
            for language in ['ru', 'en']:
                label = f'{width}x{height}-{scheme}-{language}'
                context = browser.new_context(viewport={'width': width, 'height': height},
                                              color_scheme=scheme, reduced_motion='reduce')
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
                    content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(scheme))))
                context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
                context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
                context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                held = []
                page.route('**/api/v1/config', lambda route: held.append(route))
                page.goto(url, wait_until='domcontentloaded')
                expect(page.locator('.loading-view')).to_be_visible()
                expect(page.locator('.loading-view.ui-panel .ui-headline')).to_be_visible()
                expect(page.locator('.loading-bar.ui-progress > span')).to_have_count(1)
                page.evaluate('shellTheme(%s)' % json.dumps(scheme))

                def shot(state):
                    page.screenshot(path=str(artifacts / f'{label}-{state}.png'))

                shot('loading')
                illustrated_scope(page, 'loading', label + '-loading')
                page.wait_for_function('document.querySelector(".loading-view") !== null')
                assert held
                held.pop().fulfill(status=503, json={'error': {'code': 'internal_error', 'message': 'PRIVATE'}})
                expect(page.locator('.error-view')).to_be_visible()
                expect(page.locator('.error-view.ui-panel .ui-headline')).to_be_visible()
                expect(page.locator('.error-view .ui-status[data-tone=error]')).to_be_visible()
                assert 'PRIVATE' not in page.locator('.error-view').inner_text()
                assert page.locator('[data-action=boot-retry]').bounding_box()['height'] >= 48
                shot('error')
                illustrated_scope(page, 'error', label + '-error')
                page.locator('[data-action=boot-retry]').click()
                expect(page.locator('.loading-view')).to_be_visible()
                expect(page.locator('.loading-view.ui-panel .ui-headline')).to_be_visible()
                expect(page.locator('.loading-bar.ui-progress > span')).to_have_count(1)
                expect(page.locator('[data-action=boot-retry]')).to_have_count(0)
                shot('retry-loading')
                held.pop().continue_()
                page.unroute('**/api/v1/config')
                expect(page.locator('[data-action=start-free]')).to_be_visible()
                page.wait_for_load_state('networkidle')
                shared_controls(page, label)
                illustrated_scope(page, 'arena', label + '-first')
                shot('first')

                # Initial fight provides local feedback while the real command is held.
                page.route('**/api/v1/battle/start', lambda route: held.append(route))
                page.locator('[data-action=start-free]').click()
                expect(page.locator('[data-action=start-free]')).to_be_disabled()
                expect(page.locator('[data-action=start-free]')).to_contain_text('Подожди' if language == 'ru' else 'Please wait')
                shot('first-pending')
                route = held.pop()
                response = route.fetch()
                payload = response.json()
                route.fulfill(response=response)
                page.unroute('**/api/v1/battle/start')
                battle = payload['state']['battle']
                expect(page.locator('.battle-layout')).to_be_visible()
                assert_controls(page, label + '-preparing', essential=False, bottom_inset=30, top_inset=40)
                illustrated_scope(page, 'battle', label + '-preparing')
                shot('preparing')
                clock.value = battle['starts_at']
                refresh(page)
                expect(page.locator('.battle-tap')).to_be_enabled()
                assert_controls(page, label + '-active', essential=False, bottom_inset=30, top_inset=40)
                illustrated_scope(page, 'battle', label + '-active')
                shot('active')
                clock.value = battle['ends_at'] + 1
                refresh(page)
                expect(page.locator('#battle-result-toast')).to_be_visible()
                nav_geometry(page, height, 30)
                # A state poll can replace the toast between locator resolution
                # and evaluation; measure connected nodes in one DOM snapshot.
                result_text = page.evaluate('''() => [...document.querySelectorAll('.result-toast p, .result-toast small')]
                    .map(n => ({text:n.textContent, size:parseFloat(getComputedStyle(n).fontSize)}))''')
                assert result_text and all(item['size'] >= 14 for item in result_text), (label, result_text)
                shot('result')
                page.locator('[data-action=close-result]').click()

                shell = None
                for destination in ['arena', 'gear', 'roost', 'leaderboard']:
                    tab(page, destination)
                    page.wait_for_load_state('networkidle')
                    assert page.evaluate('scrollY') == 0, (label, destination)
                    shared_controls(page, label + '-' + destination)
                    illustrated_scope(page, destination, label + '-' + destination)
                    nav_geometry(page, height, 30)
                    current = page.evaluate('''() => {
                        const css = getComputedStyle(document.querySelector('.app-shell'));
                        const lang = document.querySelector('.language-switch').getBoundingClientRect();
                        const wallet = getComputedStyle(document.querySelector('.wallet-item > div'));
                        return [css.paddingLeft, css.paddingRight, lang.width, lang.height, wallet.display];
                    }''')
                    if shell is not None:
                        assert current == shell, (label, destination, shell, current)
                    shell = current
                    shot(destination)
                    # Live environment events cannot replace content, reset scroll or money.
                    page.evaluate('scrollTo(0, 120)')
                    opposite = 'dark' if scheme == 'light' else 'light'
                    assert page.evaluate('''scheme => {
                        const node = document.querySelector('#main').firstElementChild;
                        const y = scrollY, money = document.querySelector('#balance').textContent;
                        shellTheme(scheme);
                        return node === document.querySelector('#main').firstElementChild
                            && y === scrollY && money === document.querySelector('#balance').textContent;
                    }''', opposite)
                    illustrated_scope(page, destination, label + '-' + destination + '-theme-change')
                    page.evaluate('shellTheme(%s)' % json.dumps(scheme))
                    before = page.locator('#navigation').bounding_box()
                    page.set_viewport_size({'width': width, 'height': height - 80})
                    page.evaluate("Telegram.WebApp.viewportHeight=innerHeight; shellEmit('viewportChanged', {isStateStable:false})")
                    assert page.locator('#navigation').bounding_box() == before
                    page.evaluate("Telegram.WebApp.viewportStableHeight=innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    nav_geometry(page, height - 80, 30)
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate("Telegram.WebApp.viewportStableHeight=innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    nav_geometry(page, height, 30)
                # Generic command recovery uses readable, touch-sized shared feedback.
                tab(page, 'arena')
                illustrated_scope(page, 'arena', label + '-back-to-arena')
                page.route('**/api/v1/queue/join', lambda route: route.abort(), times=1)
                page.locator('[data-action=arena-mode][data-mode=online]').click()
                page.locator('[data-action=queue-join]').click()
                expect(page.locator('.notice.error')).to_be_visible()
                assert page.locator('.notice').evaluate('n => parseFloat(getComputedStyle(n).fontSize) >= 14')
                assert page.locator('.notice button').bounding_box()['height'] >= 44
                shot('command-error')
                command(page, '[data-action=retry]', 'queue/join')
                expect(page.locator('.queue-card')).to_be_visible()
                shot('queue')
                command(page, '[data-action=queue-leave]', 'queue/leave')
                context.close()
                print('Passed:', label, flush=True)
    assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=ROOT / 'artifacts' / 'consistency-review')
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-consistency-') as temporary:
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
    print('Consistency checks passed: 20 viewport/theme/language combinations')


if __name__ == '__main__':
    main()
