"""Arena visual matrix and setup flow using the real API and a disposable DB."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from roosters import create_app
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, command, refresh, server_state, check_layout
from scripts.check_battle_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent


def snapshot(page, artifacts, label):
    page.evaluate('window.scrollTo(0, 0)')
    check_layout(page, label)
    page.screenshot(path=str(artifacts / (label + '.png')), full_page=True)
    page.screenshot(path=str(artifacts / (label + '-viewport.png')))
    return page.evaluate('''() => {
      const rect = s => document.querySelector(s)?.getBoundingClientRect().toJSON();
      return {fight: rect('.arena-fight'), nav: rect('#navigation'),
        risk: rect('.arena-risk'), fighter: rect('.arena-fighter')};
    }''')


def exercise(browser, url, clock, artifacts):
    errors, matrix = [], {}
    for width, height in [(360, 640), (390, 844), (430, 932), (360, 560), (320, 568)]:
        context = browser.new_context(viewport={'width': width, 'height': height})
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            status=200, content_type='application/javascript', body=BRIDGE))
        context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
        context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
            {'user_id': f'arena_{width}_{height}', 'name': 'Боец с длинным именем'}))
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        for language in ('ru', 'en'):
            page.locator(f'[data-language="{language}"]').click()
            for theme in ('dark', 'light'):
                page.evaluate(f"battleTheme('{theme}')")
                expect(page.locator('[data-action=start-free]')).to_be_visible()
                assert page.locator('.hero').count() == 1
                label = f'first-{width}x{height}-{language}-{theme}'
                matrix[label] = snapshot(page, artifacts, label)
        started = command(page, '[data-action=start-free]', 'battle/start')
        clock.value = started['state']['battle']['ends_at'] + 1
        refresh(page)
        expect(page.locator('.arena-fight')).to_be_visible()
        page.locator('[data-action=close-result]').click()
        for language in ('ru', 'en'):
            page.locator(f'[data-language="{language}"]').click()
            for mode in ('bot', 'online'):
                page.locator(f'[data-action=arena-mode][data-mode={mode}]').click()
                for theme in ('dark', 'light'):
                    page.evaluate(f"battleTheme('{theme}')")
                    label = f'returning-{width}x{height}-{language}-{mode}-{theme}'
                    geometry = snapshot(page, artifacts, label)
                    matrix[label] = geometry
                    assert page.locator('.hero').count() == 0
                    assert page.locator('.arena-setup .primary').count() == 1
                    assert geometry['fight']['height'] >= 48
                    # At standard phone heights the entire setup fits above navigation.
                    if width >= 360:
                        assert geometry['fight']['bottom'] <= geometry['nav']['top'], (label, geometry)
                        assert page.locator('.arena-fight').evaluate('''button => {
                          const r = button.getBoundingClientRect();
                          return button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
                        }'''), label
                    assert page.locator('.arena-risk').evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 14
                    assert page.locator('.last-battle-card').bounding_box()['y'] > geometry['fight']['bottom']
        # Real stake changes, reload, queue and cancellation preserve selections.
        page.locator('[data-action=stake][data-stake-minor="2500"]').click()
        expect(page.locator('.arena-risk')).to_contain_text('47.5')
        page.reload(wait_until='networkidle')
        expect(page.locator('[data-mode=online]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('[data-stake-minor="2500"]')).to_have_attribute('aria-pressed', 'true')
        before = server_state(page)['player']['balance_minor']
        queued = command(page, '[data-action=queue-join]', 'queue/join')
        assert queued['state']['player']['balance_minor'] == before
        command(page, '[data-action=queue-leave]', 'queue/leave')
        expect(page.locator('.arena-fight')).to_be_enabled()
        if width == 390:
            # Bridge insets change live; natural scroll still reaches the action.
            page.evaluate('''() => {
              const s = document.documentElement.style;
              s.setProperty('--tg-safe-area-inset-top', '24px');
              s.setProperty('--tg-content-safe-area-inset-top', '20px');
              s.setProperty('--tg-safe-area-inset-bottom', '24px');
              s.setProperty('--tg-content-safe-area-inset-bottom', '10px');
            }''')
            snapshot(page, artifacts, 'returning-safe-areas')
            page.locator('.arena-fight').scroll_into_view_if_needed()
            page.evaluate('document.documentElement.removeAttribute("style")')
            # Insufficient funds from a real purchase and a larger stake.
            page.locator('[data-tab=gear]').click()
            for _ in range(2):
                command(page, '[data-action=upgrade][data-slot=sword]', 'gear/upgrade')
            page.locator('[data-tab=arena]').click()
            page.locator('[data-action=stake][data-stake-minor="10000"]').click()
            expect(page.locator('.arena-fight')).to_be_disabled()
            expect(page.locator('.arena-unavailable')).to_be_visible()
            snapshot(page, artifacts, 'returning-insufficient-funds')
            page.locator('[data-action=stake][data-stake-minor="1000"]').click()
            expect(page.locator('.arena-fight')).to_be_enabled()
        context.close()
    assert not errors, errors
    (artifacts / 'matrix.json').write_text(json.dumps(matrix, indent=2))
    print('Arena matrix passed:', len(matrix), 'screens; real queue/reload/selection and funds checks.')
    for label, geometry in matrix.items():
        if geometry['fight'] and label.endswith('bot-dark'):
            print(label, 'Fight bottom:', round(geometry['fight']['bottom']), 'Navigation top:', round(geometry['nav']['top']))


def main():
    artifacts = ROOT / 'artifacts' / 'arena-review'
    artifacts.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-arena-') as temp:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='test' * 16,
            database_path=str(Path(temp) / 'game.sqlite3')), clock=clock, random_float=lambda: clock.draw)
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = p.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
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
