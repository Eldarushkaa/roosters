"""Rankings-only mobile matrix. Real auth/state, controlled leaderboard responses.

Run: python -m scripts.check_rankings_browser. Uses a disposable database.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, check_layout, server_state
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent


def board(pid, position=2, large=False):
    names = ['Северный Петух', 'Copper champion', 'ОченьДлинноеИмяБойцаБезПробелов', 'The undefeated rooster of the entire neighbourhood']
    players = [dict(id=f'dev:rank-{i}', name=names[i % len(names)] if i < 4 else f'Fighter {i + 1}',
                    level=i + 1, power=(987654321000 if large else 9876) - i * 100, pvp_wins=i * 12345) for i in range(50)]
    if position is not None:
        players[position - 1].update(id=pid, name='Мой боец / My fighter')
    return {'scope': 'development', 'power': players, 'pvp_wins': list(reversed(players))}


def geometry(page, label):
    check_layout(page, label)
    assert page.locator('.rankings-screen button').evaluate_all('nodes => nodes.every(n => n.getBoundingClientRect().height >= 44)')
    assert page.locator('.rankings-row').evaluate_all('''nodes => nodes.every(n => {
        const [rank, name, value] = [...n.children].map(c => c.getBoundingClientRect());
        return rank.right <= name.left && (n.classList.contains('has-long-value') ? name.bottom <= value.top : name.right <= value.left) && value.right <= n.getBoundingClientRect().right;
    })'''), label
    assert page.locator('.rankings-row .leaderboard-name, .rankings-value').evaluate_all('nodes => nodes.every(n => parseFloat(getComputedStyle(n).fontSize) >= 16)')


def exercise(browser, url, artifacts):
    errors = []
    for width, height in [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]:
        for scheme in ['dark', 'light']:
            for language in ['ru', 'en']:
                label = f'{width}x{height}-{scheme}-{language}'
                context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, content_type='application/javascript', body=BRIDGE.replace('SCHEME', json.dumps(scheme))))
                context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
                context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
                context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                held, requests = [], []
                def capture(route):
                    requests.append((route.request.method, route.request.url.split('/api/v1')[-1]))
                    held.append(route)
                page.route('**/api/v1/leaderboard', capture)
                page.goto(url + '/#leaderboard', wait_until='domcontentloaded')
                expect(page.locator('.rankings-skeleton')).to_be_visible()
                page.evaluate('shellTheme(%s)' % json.dumps(scheme))
                pid = server_state(page)['player']['id']
                def shot(state):
                    page.screenshot(path=str(artifacts / f'{label}-{state}.png'))
                def resolve(payload=None, fail=False):
                    page.wait_for_function('document.querySelector(".rankings-results").getAttribute("aria-busy") === "true"')
                    assert len(held) == 1
                    route = held.pop()
                    route.fulfill(status=503 if fail else 200, json={'error': {'code': 'internal_error', 'message': 'PRIVATE backend detail'}} if fail else payload)
                    expect(page.locator('.rankings-results')).to_have_attribute('aria-busy', 'false')
                def refresh():
                    page.locator('.rankings-refresh').click()
                    expect(page.locator('.rankings-results')).to_have_attribute('aria-busy', 'true')
                    expect(page.locator('.rankings-refresh')).to_be_disabled()
                shot('loading')
                resolve(fail=True)
                expect(page.locator('.rankings-empty')).to_be_visible()
                assert 'PRIVATE' not in page.locator('.rankings-screen').inner_text()
                shot('initial-error')
                page.locator('.rankings-status button').click()
                data = board(pid)
                resolve(data)
                expect(page.locator('.rankings-row')).to_have_count(50)
                expect(page.locator('#rankings-self')).to_contain_text(data['power'][1]['name'])
                assert page.locator('.rankings-own').count() == 0
                geometry(page, label)
                shot('power-top')
                # Browser fixtures must retain server order and existing locale formatting.
                def check_rows(mode):
                    rendered = page.locator('.rankings-row').evaluate_all("nodes => nodes.map(n => ({name: n.querySelector('.leaderboard-name').firstChild.textContent, value: n.querySelector('.rankings-value').lastChild.textContent}))")
                    for actual, expected in zip(rendered, data[mode]):
                        assert actual['name'] == expected['name']
                        formatted = page.evaluate('(x) => new Intl.NumberFormat(x.locale).format(x.value)', {'locale': 'ru-RU' if language == 'ru' else 'en-US', 'value': expected[mode]})
                        assert actual['value'] == formatted
                check_rows('power')
                before = len(requests)
                page.locator('[data-ranking=pvp_wins]').click()
                expect(page.locator('[data-ranking=pvp_wins]')).to_have_attribute('aria-pressed', 'true')
                assert len(requests) == before, 'Switching ranking must not add a request'
                check_rows('pvp_wins')
                geometry(page, label)
                shot('pvp-far')
                page.locator('.rankings-own').click()
                assert page.locator('#rankings-self').evaluate('''n => {
                    const r = n.getBoundingClientRect(), nav = document.querySelector('#navigation').getBoundingClientRect();
                    return document.activeElement === n && r.top >= 40 && r.bottom <= nav.top;
                }'''), label
                shot('own-position')
                page.locator('[data-ranking=power]').click()
                refresh()
                expect(page.locator('.rankings-row')).to_have_count(50)
                shot('refreshing')
                resolve(fail=True)
                expect(page.locator('.rankings-row')).to_have_count(50)
                expect(page.locator('.rankings-status.is-error')).to_be_visible()
                assert 'PRIVATE' not in page.locator('.rankings-screen').inner_text()
                shot('stale')
                page.locator('.rankings-status button').click()
                data = board(pid, position=36)
                resolve(data)
                page.locator('.rankings-own').click()
                expect(page.locator('#rankings-self .rankings-rank')).to_have_text('36')
                refresh()
                data = board(pid, position=None, large=True)
                resolve(data)
                expect(page.locator('.rankings-outside')).to_be_visible()
                assert page.locator('#rankings-self, .rankings-own').count() == 0
                shot('outside')
                page.locator('.rankings-columns').evaluate("n => n.scrollIntoView({block:'start'})")
                page.evaluate('scrollBy(0, -48)')
                geometry(page, label)
                shot('large-values')
                refresh()
                resolve({'scope': 'development', 'power': [], 'pvp_wins': []})
                expect(page.locator('.rankings-empty')).to_be_visible()
                assert page.locator('.rankings-row').count() == 0
                shot('empty')
                refresh()
                resolve(board(pid))
                expect(page.locator('.rankings-row')).to_have_count(50)
                # The last row can clear navigation and all simulated native insets.
                page.locator('.rankings-row').last.evaluate("n => n.scrollIntoView({block:'center'})")
                assert page.locator('.rankings-row').last.evaluate('n => n.getBoundingClientRect().bottom < document.querySelector("#navigation").getBoundingClientRect().top')
                geometry(page, label)
                assert all(item == ('GET', '/leaderboard') for item in requests)
                assert page.locator('html').get_attribute('data-theme') == scheme
                context.close()
                print('Passed:', label, flush=True)
    assert not errors, errors


def main():
    artifacts = ROOT / 'artifacts' / 'rankings-layout'
    artifacts.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='roosters-rankings-') as temporary:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='test' * 16,
                                 database_path=str(Path(temporary) / 'game.sqlite3')), clock=Clock())
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = playwright.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
                try:
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', artifacts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Rankings checks passed: 20 viewport/theme/language combinations; screenshots:', artifacts)


if __name__ == '__main__':
    main()
