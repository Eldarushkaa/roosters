"""Custom-stake quotes, stale responses, balance clamps and all-in commands.

Uses a disposable backend and wallet. Run: python -m scripts.check_stake_browser.
The Arena checker owns the full RU/EN, theme, size and pointer-drag matrix.
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app, wallet
from roosters.config import Settings
from scripts.check_arena_browser import assert_stake_display, snapshot, stake_text
from scripts.check_battle_browser import BRIDGE
from scripts.check_browser import Clock, QuietRequests, command, refresh, server_state

ROOT = Path(__file__).resolve().parent.parent


def choose_stake(page, minor):
    """Set an exact minor-unit value; actual drag/keyboard is checked in Arena."""
    page.evaluate('''minor => {
        const range = document.querySelector('#arena-stake-range');
        range.value = String(minor);
        range.dispatchEvent(new Event('input', {bubbles:true}));
        range.dispatchEvent(new Event('change', {bubbles:true}));
    }''', minor)
    assert_stake_display(page, minor)


def quote_request(request, minor):
    return request.method == 'GET' and request.url.endswith('/battle/quote?stake_minor=' + str(minor))


def quote(page, minor):
    return page.evaluate('''async minor => {
        const response = await fetch('/api/v1/battle/quote?stake_minor=' + minor, {
            headers: {Authorization: 'Bearer ' + sessionStorage.getItem('rooster.v1.token')}
        });
        if (!response.ok) throw Error('Quote failed: ' + response.status);
        return response.json();
    }''', minor)


def check_online_quote(page, expected):
    expect(page.locator('#arena-mode-help')).to_be_visible()
    values = page.locator('#arena-mode-help .arena-risk strong')
    expect(values.nth(0)).to_contain_text('−' + stake_text(page, expected['stake_minor']))
    expect(values.nth(1)).to_contain_text(stake_text(page, expected['online']['win_payout_minor']))


def set_balance(app, page, clock, minor):
    player = server_state(page)['player']
    with app.extensions['database'].transaction() as db:
        wallet.change(db, player['id'], minor - player['balance_minor'], 'browser_fixture', str(uuid4()), clock.value)
    refresh(page)
    page.wait_for_load_state('networkidle')


def exercise(browser, url, app, clock, artifacts):
    contexts, errors = [], []

    def player(identity):
        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        contexts.append(context)
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            content_type='application/javascript', body=BRIDGE))
        context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
        context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
            {'user_id': identity, 'name': 'Custom stake ' + identity}))
        context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        return page

    page = player('stake_a')
    try:
        introduced = command(page, '[data-action=start-free]', 'battle/start')['state']['battle']
        clock.value = introduced['ends_at'] + 1
        refresh(page)
        expect(page.locator('.arena-fight')).to_be_visible()
        page.locator('[data-action=close-result]').click()
        page.locator('[data-action=arena-mode][data-mode=online]').click()
        page.locator('[data-action=mode-help][data-mode=online]').click()

        mutations = []

        def record(request):
            if request.method == 'POST' and '/api/v1/' in request.url and not request.url.endswith('/presence'):
                mutations.append(request.url)

        page.on('request', record)
        before = server_state(page)['player']['balance_minor']
        held = []

        def hold_old_quote(route):
            held.append((route, route.fetch()))

        # A pending quote disables entry. A newer selection may complete before
        # the old quote; delivering the old body must not change the new prize.
        page.route('**/api/v1/battle/quote?stake_minor=2501', hold_old_quote, times=1)
        with page.expect_request(lambda request: quote_request(request, 2501)):
            choose_stake(page, 2501)
        expect(page.locator('.arena-fight')).to_be_disabled()
        expect(page.locator('.arena-quote-status')).to_be_visible()
        page.locator('.arena-fight').evaluate('button => button.click()')
        with page.expect_response(lambda response: quote_request(response.request, 7501)):
            choose_stake(page, 7501)
        expect(page.locator('.arena-fight')).to_be_enabled()
        expected = quote(page, 7501)
        check_online_quote(page, expected)
        assert len(held) == 1
        held[0][0].fulfill(response=held[0][1])
        page.wait_for_load_state('networkidle')
        assert_stake_display(page, 7501)
        check_online_quote(page, expected)
        snapshot(page, artifacts, 'custom-quote-stale-response')

        # Failed preview remains recoverable without an economic command.
        page.route('**/api/v1/battle/quote?stake_minor=12555', lambda route: route.abort('failed'), times=1)
        with page.expect_request(lambda request: quote_request(request, 12555)):
            choose_stake(page, 12555)
        expect(page.locator('[data-action=retry-stake-quote]')).to_be_visible()
        expect(page.locator('.arena-fight')).to_be_disabled()
        page.locator('.arena-fight').evaluate('button => button.click()')
        assert_stake_display(page, 12555)
        snapshot(page, artifacts, 'custom-quote-error')
        with page.expect_response(lambda response: quote_request(response.request, 12555)):
            page.locator('[data-action=retry-stake-quote]').click()
        expect(page.locator('.arena-fight')).to_be_enabled()
        check_online_quote(page, quote(page, 12555))
        assert not mutations, mutations
        assert server_state(page)['player']['balance_minor'] == before
        page.remove_listener('request', record)

        # Bot preview and the committed wager use the same custom selection.
        page.locator('[data-action=arena-mode][data-mode=bot]').click()
        page.locator('[data-action=mode-help][data-mode=bot]').click()
        bot_quote = quote(page, 12555)
        payout = page.locator('#arena-mode-help .arena-risk strong').nth(1)
        expect(payout).to_contain_text(stake_text(page, bot_quote['bot']['min_payout_minor']))
        expect(payout).to_contain_text(stake_text(page, bot_quote['bot']['max_payout_minor']))
        custom_bot = command(page, '[data-action=start-bot]', 'battle/start')['state']
        assert custom_bot['battle']['wager']['stake_minor'] == 12555
        assert custom_bot['player']['balance_minor'] == before - 12555
        assert bot_quote['bot']['min_payout_minor'] <= custom_bot['battle']['wager']['win_payout_minor'] <= bot_quote['bot']['max_payout_minor']
        clock.value = custom_bot['battle']['ends_at'] + 1
        refresh(page)
        expect(page.locator('.arena-fight')).to_be_visible()
        page.locator('[data-action=close-result]').click()
        page.locator('[data-action=arena-mode][data-mode=online]').click()
        page.locator('[data-action=mode-help][data-mode=online]').click()

        # An external wallet update clamps an existing custom selection. The
        # chosen maximum follows the server's spendable balance to the cent.
        set_balance(app, page, clock, 10001)
        expect(page.locator('#arena-stake-range')).to_have_attribute('max', '10001')
        assert_stake_display(page, 10001)
        expect(page.locator('.arena-fight')).to_be_enabled()
        check_online_quote(page, quote(page, 10001))
        page.reload(wait_until='networkidle')
        assert_stake_display(page, 10001)
        expect(page.locator('.arena-fight')).to_be_enabled()

        # The real online command sends exactly the all-in minor-unit amount;
        # the opponent enters with a different decimal stake.
        queued = command(page, '[data-action=queue-join]', 'queue/join')
        assert queued['state']['queue']['stake_minor'] == 10001
        assert queued['state']['player']['balance_minor'] == 10001
        opponent = player('stake_b')
        choose_stake(opponent, 2501)
        expect(opponent.locator('[data-action=queue-join]')).to_be_enabled()
        matched = command(opponent, '[data-action=queue-join]', 'queue/join')
        assert matched['result']['matched']
        # Display time is frozen for screenshots, so explicitly run the waiting
        # player's recovery path instead of expecting its timed poll to fire.
        refresh(page)
        expect(page.locator('.battle-card')).to_be_visible()
        mine, theirs = server_state(page), server_state(opponent)
        assert mine['player']['balance_minor'] == 0
        assert theirs['player']['balance_minor'] == 42000 - 2501
        assert mine['battle']['wager'] == {'stake_minor': 10001, 'win_payout_minor': 19001}
        assert theirs['battle']['wager'] == {'stake_minor': 2501, 'win_payout_minor': 4751}
        clock.value = mine['battle']['ends_at'] + 1
        refresh(page)
        refresh(opponent)
        finished = server_state(page)
        assert finished['battle']['result']['won']
        assert finished['battle']['result']['payout_minor'] == 19001
        assert finished['player']['balance_minor'] == 19001
        page.locator('[data-action=close-result]').click()

        # Below the minimum, even synthetic clicks on disabled presets cannot
        # create a quote or enable a paid command. Exactly the minimum is valid.
        set_balance(app, page, clock, 999)
        expect(page.locator('#arena-stake-range')).to_be_disabled()
        expect(page.locator('.arena-fight')).to_be_disabled()
        assert page.locator('[data-action=stake]:enabled').count() == 0
        page.locator('[data-action=stake][data-stake-minor="1000"]').evaluate('button => button.click()')
        expect(page.locator('.arena-fight')).to_be_disabled()
        snapshot(page, artifacts, 'custom-stake-below-minimum')
        set_balance(app, page, clock, 1000)
        assert_stake_display(page, 1000)
        expect(page.locator('#arena-stake-range')).to_be_disabled()
        expect(page.locator('[data-action=stake][data-stake-minor="1000"]')).to_be_enabled()
        expect(page.locator('[data-action=stake][data-stake-minor="2500"]')).to_be_disabled()
        expect(page.locator('.arena-fight')).to_be_enabled()
        assert not errors, errors
    finally:
        for context in contexts:
            context.close()


def main():
    artifacts = ROOT / 'artifacts' / 'stake-review'
    artifacts.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-stake-') as temp:
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
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', app, clock, artifacts)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Stake browser passed: stale/error/retry quotes, no accidental mutations, balance clamp, custom reload, decimal all-in PvP and minimum boundary.')


if __name__ == '__main__':
    main()
