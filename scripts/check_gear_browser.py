"""Gear-only visual matrix and purchase recovery on a disposable real backend.

Run: python -m scripts.check_gear_browser. No real player data or Telegram login.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, server_state, tab
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent


def seed(app, page, **changes):
    pid = server_state(page)['player']['id']
    with app.extensions['database'].transaction() as db:
        for key, value in changes.items():
            assert key in {'balance_minor', 'gear', 'owned_breeds', 'breed_id'}
            db.execute(f'UPDATE players SET {key}=? WHERE id=?', (json.dumps(value) if isinstance(value, (dict, list)) else value, pid))
        app.extensions['game']._refresh_power(db, pid)
    refresh(page)
    page.wait_for_load_state('networkidle')


def check_controls(page, label):
    check_layout(page, label)
    assert page.locator('.gear-screen button').evaluate_all('nodes => nodes.every(n => n.getBoundingClientRect().height >= 44)')
    assert page.locator('.gear-screen p').evaluate_all("nodes => nodes.every(n => parseFloat(getComputedStyle(n).fontSize) >= 14)")
    # Every action can be scrolled clear of native top chrome and bottom nav.
    for button in page.locator('.gear-screen button').all():
        button.evaluate("n => n.scrollIntoView({block:'center'})")
        assert button.evaluate('''n => {
            const r = n.getBoundingClientRect();
            return [0.15, 0.5, 0.85].every(y => n.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height * y)));
        }'''), label


def exercise(browser, url, app, artifacts):
    errors = []
    for width, height in [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]:
        for scheme in ['dark', 'light']:
            for language in ['ru', 'en']:
                label = f'{width}x{height}-{scheme}-{language}'
                context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(scheme))))
                context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
                # Keep scheduled presence out of deterministic purchase assertions.
                context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url + '/#gear', wait_until='networkidle')
                page.evaluate('shellTheme(%s)' % json.dumps(scheme))
                expect(page.locator('.gear-screen')).to_be_visible()
                seed(app, page, balance_minor=8011, gear={'helmet': 10, 'armor': 1, 'sword': 0}, owned_breeds=['yard', 'copper'])
                expect(page.locator('[data-slot-id=helmet]')).to_have_attribute('data-state', 'complete')
                expect(page.locator('[data-slot-id=armor]')).to_have_attribute('data-state', 'funds')
                expect(page.locator('[data-slot-id=sword]')).to_have_attribute('data-state', 'ready')
                assert ('239,89' if language == 'ru' else '239.89') in page.locator('[data-slot-id=armor]').inner_text()
                assert page.locator('[data-breed-id=yard] button').count() == 0
                expect(page.locator('[data-breed-id=copper] button')).to_be_enabled()
                expect(page.locator('[data-breed-id=storm] button')).to_be_disabled()
                check_controls(page, label)
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(artifacts / f'{label}-full.png'), full_page=True)
                for section, selector in [('upgrades', '[data-slot-id=armor]'), ('breeds', '[data-breed-id=storm]')]:
                    page.locator(selector).evaluate("n => n.scrollIntoView({block:'center', behavior:'instant'})")
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    page.screenshot(path=str(artifacts / f'{label}-{section}.png'))
                context.close()
                print('Passed:', label, flush=True)

    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, body=''))
    context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
    page = context.new_page()
    page.goto(url + '/#gear', wait_until='networkidle')
    seed(app, page, balance_minor=100000)
    before = server_state(page)
    sent = []
    held = []
    page.route('**/api/v1/gear/upgrade', lambda route: held.append(route))
    page.locator('[data-slot=sword]').click()
    expect(page.locator('[data-slot-id=sword]')).to_have_attribute('aria-busy', 'true')
    expect(page.locator('[data-slot-id=helmet]')).to_have_attribute('aria-busy', 'false')
    expect(page.locator('[data-slot-id=helmet]')).to_have_attribute('data-state', 'waiting')
    expect(page.locator('[data-tab=roost]')).to_be_enabled()
    page.screenshot(path=str(artifacts / 'purchase-loading.png'))
    route = held.pop()
    sent.append((route.request.post_data_json, route.request.headers['idempotency-key']))
    result = route.fetch().json()
    assert sent[0][0] == {'slot': 'sword'}
    assert result['state']['player']['balance_minor'] == before['player']['balance_minor'] - before['economy']['upgrade_costs_minor']['sword']
    route.abort()  # Response lost after committing the purchase.
    expect(page.locator('[data-slot-id=sword]')).to_have_attribute('data-state', 'retry')
    expect(page.locator('[data-slot-id=sword] .gear-feedback')).to_be_visible()
    assert page.locator('.upgrade-row').count() == 3
    page.screenshot(path=str(artifacts / 'purchase-retry.png'))
    page.unroute('**/api/v1/gear/upgrade')
    with page.expect_response(lambda response: response.url.endswith('/gear/upgrade')) as received:
        page.locator('[data-slot-id=sword] [data-action=gear-retry]').click()
    retry = received.value
    assert (retry.request.post_data_json, retry.request.headers['idempotency-key']) == sent[0]
    expect(page.locator('[data-slot-id=sword] .gear-feedback')).to_contain_text('Улучшение применено')
    after = server_state(page)
    assert after['player']['balance_minor'] == result['state']['player']['balance_minor']
    assert after['player']['gear']['sword'] == 1
    for breed, cost in [('copper', 50000), ('yard', 0), ('copper', 0)]:
        before = server_state(page)
        with page.expect_response(lambda response: response.url.endswith('/breed/buy')) as received:
            page.locator(f'[data-breed="{breed}"]').click()
        response = received.value
        assert response.request.post_data_json == {'breed_id': breed}
        assert response.json()['state']['player']['balance_minor'] == before['player']['balance_minor'] - cost
        expect(page.locator(f'[data-breed-id={breed}]')).to_have_class('gear-breed is-equipped')
        assert page.locator(f'[data-breed-id={breed}] button').count() == 0
    # A definitive API error stays local, retains useful screen data and permits recovery.
    page.route('**/api/v1/gear/upgrade', lambda route: route.fulfill(status=409, json={'error': {'code': 'insufficient_funds', 'message': 'server detail'}}))
    page.locator('[data-slot=helmet]').click()
    expect(page.locator('[data-slot-id=helmet] .gear-feedback.is-error')).to_be_visible()
    assert 'server detail' not in page.locator('.gear-screen').inner_text()
    page.unroute('**/api/v1/gear/upgrade')
    tab(page, 'arena')
    command(page, '[data-action=queue-join]', 'queue/join')
    tab(page, 'gear')
    expect(page.locator('[data-slot-id=helmet]')).to_have_attribute('data-state', 'locked')
    expect(page.locator('[data-breed-id=yard]')).to_have_attribute('data-state', 'locked')
    page.screenshot(path=str(artifacts / 'search-locked.png'))
    context.close()
    assert not errors, errors
    print('Passed: real upgrade, buy/equip, immutable retry after lost response, local loading/error, queue lock', flush=True)


def main():
    artifacts = ROOT / 'artifacts' / 'gear-layout'
    artifacts.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='roosters-gear-') as temporary:
        clock = Clock()
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='test' * 16, database_path=str(Path(temporary) / 'game.sqlite3')), clock=clock)
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = playwright.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
                try:
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', app, artifacts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Gear checks passed; screenshots:', artifacts)


if __name__ == '__main__':
    main()
