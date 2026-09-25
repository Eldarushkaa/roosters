"""Shell/theme/viewport regression matrix; real game API, simulated native bridge.

Run: python -m scripts.check_shell_browser. Never uses a real player database.
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
from scripts.check_browser import Clock, QuietRequests, command, refresh, tab, check_layout
from scripts.check_battle_browser import assert_controls

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = """
const callbacks = {}, nativeCalls = [];
window.Telegram = {WebApp: {
  platform: 'ios', version: '8.0', colorScheme: SCHEME,
  themeParams: {}, viewportStableHeight: innerHeight, viewportHeight: innerHeight,
  safeAreaInset: {top: 24, bottom: 20, left: 4, right: 6},
  contentSafeAreaInset: {top: 16, bottom: 10, left: 2, right: 2},
  isVersionAtLeast: () => true,
  ready() { nativeCalls.push(['ready']); }, expand() { nativeCalls.push(['expand']); },
  setHeaderColor(c) { nativeCalls.push(['header', c]); },
  setBackgroundColor(c) { nativeCalls.push(['background', c]); },
  setBottomBarColor(c) { nativeCalls.push(['bottom', c]); },
  onEvent(name, fn) { callbacks[name] = fn; }
}};
window.shellEmit = (name, args) => callbacks[name]?.(args);
window.shellTheme = scheme => {
  Telegram.WebApp.colorScheme = scheme;
  Telegram.WebApp.themeParams = scheme === 'light'
    ? {bg_color: '#f7f5ef', secondary_bg_color: '#ffffff', text_color: '#222222', hint_color: '#666666'}
    : {bg_color: '#17212b', secondary_bg_color: '#202d3a', text_color: '#eeeeee', hint_color: '#aaaaaa'};
  shellEmit('themeChanged');
};
window.shellCalls = nativeCalls;
"""


def nav_geometry(page, height, inset):
    check_layout(page, 'shell')
    box = page.locator('#navigation').bounding_box()
    assert box and abs(box['y'] + box['height'] - (height - inset - 16)) < 1, box
    assert page.locator('#navigation button').first.bounding_box()['height'] >= 44
    return box


def theme(page, scheme, telegram, preserve=False):
    if telegram:
        assert page.evaluate("""({scheme, preserve}) => {
            const before = shellSnapshot();
            shellTheme(scheme);
            return !preserve || shellSame(before, shellSnapshot());
        }""", {'scheme': scheme, 'preserve': preserve})
    else:
        if preserve:
            page.evaluate("""() => {
                window.shellThemeStable = null;
                matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
                    window.shellThemeStable = shellSame(shellThemeBefore, shellSnapshot());
                }, {once: true});
            }""")
        page.emulate_media(color_scheme=scheme)
        if preserve:
            page.wait_for_function('() => shellThemeStable !== null')
            assert page.evaluate('shellThemeStable')
    expect(page.locator('html')).to_have_attribute('data-theme', scheme)


def exercise(browser, url, clock, artifacts):
    errors = []
    for width, height in [(360, 560), (360, 640), (390, 844), (430, 932), (320, 568)]:
        for telegram in [False, True]:
            for scheme in ['light', 'dark']:
                label = f'{width}x{height}-{"telegram" if telegram else "browser"}-{scheme}'
                context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
                    status=200, content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(scheme)) if telegram else ''))
                # Observe the exact media event, excluding independent visibility/
                # server refreshes Chrome can deliver around emulation commands.
                context.add_init_script("""
                    window.shellSnapshot = () => ({
                        node: document.querySelector('#main')?.firstElementChild,
                        fighter: document.querySelector('[data-fighter=you]'),
                        scroll: scrollY,
                        battleScroll: document.querySelector('.battle-overview')?.scrollTop || 0,
                    });
                    window.shellSame = (a, b) => a.node === b.node && a.fighter === b.fighter
                        && Math.abs(a.scroll - b.scroll) < 1 && Math.abs(a.battleScroll - b.battleScroll) < 1;
                    const shellMedia = matchMedia('(prefers-color-scheme: dark)');
                    shellMedia.addEventListener('change', () => {
                        window.shellThemeBefore = shellSnapshot();
                    });
                """)
                context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
                context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
                    {'user_id': label, 'name': 'Игрок оболочки'}))
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url, wait_until='networkidle')
                theme(page, scheme, telegram)
                first = command(page, '[data-action=start-free]', 'battle/start')['state']['battle']
                clock.value = first['ends_at'] + 1
                refresh(page)
                expect(page.locator('.arena-returning')).to_be_visible()
                # Dismiss the result before taking the returning-player screenshot.
                page.locator('[data-action=close-result]').click()
                inset = 30 if telegram else 0
                nav_geometry(page, height, inset)
                page.screenshot(path=str(artifacts / f'{label}-arena.png'))
                page.locator('.arena-fight').evaluate("node => node.scrollIntoView({block: 'center'})")
                assert page.locator('.arena-fight').evaluate('''node => {
                    const r = node.getBoundingClientRect();
                    return [0.1, 0.5, 0.9].every(y => node.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height * y)));
                }'''), label + ': Arena action obscured after scrolling'
                if height <= 640:
                    page.screenshot(path=str(artifacts / f'{label}-arena-action.png'))
                page.wait_for_load_state('networkidle')
                # Environment events must not reset scroll or replace screen DOM.
                page.evaluate("window.scrollTo(0, 140)")
                opposite = 'dark' if scheme == 'light' else 'light'
                theme(page, opposite, telegram, preserve=True)
                theme(page, scheme, telegram)
                if telegram:
                    scroll = page.evaluate('scrollY')
                    before = nav_geometry(page, height, inset)
                    # Native webview resize may occur *during* the gesture too.
                    page.set_viewport_size({'width': width, 'height': height - 80})
                    page.evaluate("Telegram.WebApp.viewportHeight -= 80; shellEmit('viewportChanged', {isStateStable:false})")
                    assert page.locator('#navigation').bounding_box() == before
                    assert page.evaluate('scrollY') == scroll
                    page.evaluate("Telegram.WebApp.viewportStableHeight = innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    nav_geometry(page, height - 80, inset)
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate("Telegram.WebApp.viewportStableHeight = innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    # Explicit Telegram zero must override a previous nonzero inset.
                    page.evaluate("Telegram.WebApp.safeAreaInset.bottom = 0; shellEmit('safeAreaChanged'); Telegram.WebApp.contentSafeAreaInset.bottom = 0; shellEmit('contentSafeAreaChanged')")
                    nav_geometry(page, height, 0)
                    page.evaluate("Telegram.WebApp.safeAreaInset.bottom = 20; shellEmit('safeAreaChanged'); Telegram.WebApp.contentSafeAreaInset.bottom = 10; shellEmit('contentSafeAreaChanged')")
                    nav_geometry(page, height, inset)
                page.evaluate('window.scrollTo(0, 0)')
                battle = command(page, '[data-action=start-bot]', 'battle/start')['state']['battle']
                clock.value = battle['starts_at']
                refresh(page)
                assert_controls(page, label, essential=not telegram, bottom_inset=inset, top_inset=40 if telegram else 0)
                page.screenshot(path=str(artifacts / f'{label}-battle.png'))
                page.wait_for_load_state('networkidle')
                page.evaluate("document.querySelector('.battle-help').open = true; document.querySelector('.battle-overview').scrollTop = 50")
                theme(page, opposite, telegram, preserve=True)
                theme(page, scheme, telegram)
                if telegram:
                    before = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().toJSON()")
                    page.set_viewport_size({'width': width, 'height': height - 80})
                    page.evaluate("Telegram.WebApp.viewportHeight = innerHeight; shellEmit('viewportChanged', {isStateStable:false})")
                    after = page.evaluate("document.querySelector('.battle-tap').getBoundingClientRect().toJSON()")
                    assert all(abs(after[key] - before[key]) < 1 for key in before), (label, before, after)
                    page.evaluate("Telegram.WebApp.viewportStableHeight = innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    assert_controls(page, label + '-collapsed', essential=False, bottom_inset=inset, top_inset=40)
                    page.screenshot(path=str(artifacts / f'{label}-collapsed.png'))
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate("Telegram.WebApp.viewportStableHeight = innerHeight; shellEmit('viewportChanged', {isStateStable:true})")
                    page.evaluate("shellEmit('deactivated')")
                    with page.expect_response(lambda response: response.url.endswith('/api/v1/state')):
                        page.evaluate("shellEmit('activated')")
                clock.value = battle['ends_at'] + 1
                refresh(page)
                expect(page.locator('.battle-layout')).to_have_count(0)
                nav_geometry(page, height, inset)
                page.screenshot(path=str(artifacts / f'{label}-result.png'))
                result_box = page.locator('#battle-result-toast').bounding_box()
                assert result_box['y'] >= (40 if telegram else 0)
                assert result_box['y'] + result_box['height'] <= height - inset
                page.locator('[data-action=close-result]').click()
                # Smoke all existing destinations; only theme/shell may change.
                for destination in ['gear', 'roost', 'leaderboard', 'arena']:
                    tab(page, destination)
                    check_layout(page, label + '-' + destination)
                    nav_geometry(page, height, inset)
                    if width == 390:
                        page.screenshot(path=str(artifacts / f'{label}-{destination}.png'))
                if telegram:
                    calls = page.evaluate('shellCalls')
                    assert sum(call[0] == 'ready' for call in calls) == 1
                    assert sum(call[0] == 'expand' for call in calls) == 1
                    colors = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()")
                    for name in ['header', 'background', 'bottom']:
                        assert [call[1] for call in calls if call[0] == name][-1] == colors
                context.close()
                print('Passed:', label, flush=True)
    assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=ROOT / 'artifacts' / 'shell-layout')
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-shell-') as temporary:
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
    print('Shell checks passed: 20 size/theme/environment combinations; screenshots:', args.artifacts_dir)


if __name__ == '__main__':
    main()
