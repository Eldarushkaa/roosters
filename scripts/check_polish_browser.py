"""Cosmetic feedback, real commands, simulated Telegram; disposable database only.

Run: python -m scripts.check_polish_browser
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
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, server_state, tab
from scripts.check_consistency_browser import SIZES
from scripts.check_gear_browser import seed
from scripts.check_shell_browser import BRIDGE, nav_geometry
from scripts.check_battle_browser import assert_controls

ROOT = Path(__file__).resolve().parent.parent
HAPTICS = """
window.hapticCalls = [];
Telegram.WebApp.HapticFeedback = {
  impactOccurred: kind => hapticCalls.push(['impact', kind]),
  notificationOccurred: kind => hapticCalls.push(['notification', kind])
};
"""


def exercise(browser, url, app, clock, artifacts):
    errors = []
    for width, height in SIZES:
        for scheme in ['light', 'dark']:
            for language in ['ru', 'en']:
                label = f'{width}x{height}-{scheme}-{language}'
                context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme,
                                              reduced_motion='no-preference')
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
                    content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(scheme)) + HAPTICS))
                context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
                context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
                context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url, wait_until='networkidle')
                page.evaluate('shellTheme(%s)' % json.dumps(scheme))

                def shot(state):
                    check_layout(page, label + '-' + state)
                    page.screenshot(path=str(artifacts / f'{label}-{state}.png'))

                assert page.evaluate('hapticCalls') == []
                # A real pointer press changes appearance immediately, without changing layout.
                pressed = page.locator('[data-language=' + language + ']')
                before = pressed.evaluate('n => [n.offsetLeft,n.offsetTop,n.offsetWidth,n.offsetHeight]')
                face_before = pressed.evaluate('n => getComputedStyle(n).boxShadow')
                pressed.hover()
                page.mouse.down()
                # Compact segmented controls depress their inset face without moving.
                assert pressed.evaluate('n => getComputedStyle(n).boxShadow') != face_before
                assert pressed.evaluate('n => [n.offsetLeft,n.offsetTop,n.offsetWidth,n.offsetHeight]') == before
                page.mouse.up()
                # Keyboard users retain a visible outline; native tab state has a check and label.
                page.keyboard.press('Tab')
                assert page.evaluate("getComputedStyle(document.activeElement).outlineWidth") == '3px'
                assert page.locator('#navigation .ui-icon').count() == 4
                assert page.locator('.ui-icon:not([aria-hidden]):not([aria-label])').count() == 0

                for outcome in ['victory', 'defeat']:
                    clock.draw = 0 if outcome == 'victory' else .999999
                    selector = '[data-action=start-free]' if outcome == 'victory' else '[data-action=start-bot]'
                    battle = command(page, selector, 'battle/start')['state']['battle']
                    expect(page.locator('.battle-tap')).to_be_disabled()
                    clock.value = battle['starts_at']
                    refresh(page)
                    expect(page.locator('.battle-tap')).to_be_enabled()
                    page.wait_for_function("hapticCalls.at(-1)?.[0] === 'impact'")
                    count = page.evaluate('hapticCalls.length')
                    page.wait_for_function("() => document.querySelector('.battle-tap').getAnimations().every(animation => animation.playState !== 'running')")
                    assert_controls(page, label + '-active', essential=False, top_inset=40, bottom_inset=30)
                    # Rapid taps retain bounded visual feedback and never vibrate per tap.
                    with page.expect_response(lambda r: r.url.endswith('/battle/tap')):
                        page.evaluate("() => { for (let i=0;i<12;i++) document.querySelector('.battle-tap').click(); }")
                    assert page.evaluate('hapticCalls.length') == count
                    assert server_state(page)['battle']['you']['taps'] == 12
                    shot('active-' + outcome)
                    clock.value = battle['ends_at'] + 1
                    refresh(page)
                    expect(page.locator('#battle-result-toast')).to_be_visible()
                    page.wait_for_function('hapticCalls.length === ' + str(count + 1))
                    assert page.evaluate('hapticCalls.at(-1)') == ['notification', 'success' if outcome == 'victory' else 'warning']
                    assert server_state(page)['battle']['result']['won'] == (outcome == 'victory')
                    shot(outcome)
                    nav_geometry(page, height, 30)
                    refresh(page)
                    page.locator('[data-language=' + language + ']').click()
                    assert page.evaluate('hapticCalls.length') == count + 1
                    page.locator('[data-action=close-result]').click()

                tab(page, 'gear')
                seed(app, page, balance_minor=100000, owned_breeds=['yard', 'copper'])
                count = page.evaluate('hapticCalls.length')
                before = server_state(page)
                result = command(page, '[data-slot=sword]', 'gear/upgrade')['state']
                expect(page.locator('.gear-feedback')).to_be_visible()
                page.wait_for_function('hapticCalls.length === ' + str(count + 1))
                assert result['player']['balance_minor'] == before['player']['balance_minor'] - before['economy']['upgrade_costs_minor']['sword']
                shot('upgrade')
                command(page, '[data-breed=copper]', 'breed/buy')
                expect(page.locator('[data-breed-id=copper]')).to_have_attribute('data-state', 'complete')
                assert page.locator('[data-breed-id=copper]').evaluate("n => n.classList.contains('is-equipped') && n.classList.contains('ui-panel')")
                page.wait_for_function('hapticCalls.length === ' + str(count + 2))
                page.locator('[data-breed-id=copper]').scroll_into_view_if_needed()
                shot('equipped')
                tab(page, 'roost')
                command(page, '[data-action=claim-daily]', 'claim/daily')
                expect(page.locator('.roost-daily .roost-feedback')).to_be_visible()
                page.wait_for_function('hapticCalls.length === ' + str(count + 3))
                shot('reward')
                # Reduced motion removes new value/reveal animations; a throwing native API
                # remains optional and cannot turn an accepted purchase into a retry.
                page.wait_for_function("() => document.getAnimations().every(a => a.playState !== 'running')")
                page.emulate_media(reduced_motion='reduce')
                page.evaluate("() => { Telegram.WebApp.HapticFeedback.impactOccurred = () => { throw Error('native unavailable'); }; }")
                tab(page, 'gear')
                command(page, '[data-slot=helmet]', 'gear/upgrade')
                expect(page.locator('[data-slot-id=helmet] .gear-feedback')).to_be_visible()
                assert page.evaluate("document.getAnimations().filter(a => a.playState === 'running').length") == 0
                assert server_state(page)['player']['gear']['helmet'] == 1
                context.close()
                print('Passed:', label, flush=True)
    assert not errors, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts-dir', type=Path, default=ROOT / 'artifacts' / 'visual-polish')
    args = parser.parse_args()
    args.artifacts_dir.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-polish-') as temporary:
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
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', app, clock, args.artifacts_dir)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Visual polish checks passed: 20 combinations; victory/defeat, purchases, rewards, haptics, reduced motion.')


if __name__ == '__main__':
    main()
