"""Roost-only visual/state checks on a disposable backend and simulated Telegram.

Run: python -m scripts.check_roost_browser. Screenshots: artifacts/roost-layout.
"""
import hashlib
import hmac
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode, parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from roosters import create_app
from roosters import wallet
from roosters.config import Settings
from scripts.check_browser import Clock, QuietRequests, check_layout, command, refresh, server_state
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent
TOKEN = 'roost-test-token'


def launch_data(clock, player):
    fields = {'auth_date': str(int(clock())), 'user': json.dumps({'id': player, 'first_name': 'Roost tester'})}
    key = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    fields['hash'] = hmac.new(key, '\n'.join(f'{k}={fields[k]}' for k in sorted(fields)).encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def context_for(browser, url, clock, player, width=390, height=844, scheme='dark', language='ru'):
    context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=scheme, reduced_motion='reduce')
    bridge = BRIDGE.replace('SCHEME', json.dumps(scheme)) + '''
        Telegram.WebApp.initData = LAUNCH;
        window.sharedLinks = [];
        Telegram.WebApp.openTelegramLink = url => sharedLinks.push(url);
    '''.replace('LAUNCH', json.dumps(launch_data(clock, player)))
    context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, content_type='application/javascript', body=bridge))
    context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
    context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
    context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
    page = context.new_page()
    page.goto(url + '/#roost', wait_until='networkidle')
    page.evaluate('shellTheme(%s)' % json.dumps(scheme))
    expect(page.locator('.roost-screen')).to_be_visible()
    return context, page


def seed(app, page, **changes):
    pid = server_state(page)['player']['id']
    with app.extensions['database'].transaction() as db:
        for key, value in changes.items():
            assert key in {'passive_at', 'daily_at', 'xp', 'battles', 'pvp_wins'}
            db.execute(f'UPDATE players SET {key}=? WHERE id=?', (value, pid))
        app.extensions['game']._refresh_power(db, pid)
    refresh(page)
    page.wait_for_load_state('networkidle')


def layout(page, label):
    for notice in page.locator('[data-action=dismiss-notice]').all():
        notice.click()
    check_layout(page, label)
    assert page.locator('.roost-screen p, .roost-screen dt').evaluate_all('nodes => nodes.every(n => parseFloat(getComputedStyle(n).fontSize) >= 14)')
    # A state refresh can replace controls during layout; measure after the
    # rendered buttons have settled, retaining the exact 44px requirement.
    page.wait_for_function("[...document.querySelectorAll('.roost-screen button')].every(n => n.getBoundingClientRect().height >= 44)")
    assert page.locator('.roost-screen .ui-button.ui-primary').count() <= 1
    assert page.locator('.roost-screen .btn').count() == 0
    # State refreshes can replace a button between scrolling and retaining its
    # handle. Measure scroll clearance atomically against the current DOM.
    obscured = page.evaluate('''() => [...document.querySelectorAll('.roost-screen button, .roost-event')].filter(n => {
        n.scrollIntoView({block:'center', behavior:'instant'});
        const r = n.getBoundingClientRect();
        return ![0.15, 0.5, 0.85].every(y => n.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height * y)));
    }).map(n => n.outerHTML)''')
    assert not obscured, (label, obscured)


def seed_reward_events(app, page, clock, player):
    state = server_state(page)
    pid = state['player']['id']
    friend = f'tg:roost-friend-{player}'
    app.extensions['game'].register(friend, 'Alex <hero>', False, state['player']['referral_code'])
    with app.extensions['database'].transaction() as db:
        wallet.change(db, pid, 30000, 'referral', friend, clock())
        wallet.change(db, pid, 12345, 'battle_reward', f'roost-win-{player}', clock())
        wallet.change(db, pid, 0, 'battle_reward', f'roost-loss-{player}', clock())
    refresh(page)
    rows = page.locator('.roost-event')
    expect(rows).to_have_count(4)
    assert rows.evaluate_all('nodes => nodes.map(n => Number(n.dataset.rewardEvent))') == [event['id'] for event in server_state(page)['reward_events']]
    expect(rows.nth(0).locator('.roost-event-amount')).to_have_text('0')
    expect(page.locator('.roost-event-friend').first).to_have_text('Alex <hero>')
    assert page.locator('.roost-events hero, .roost-events script').count() == 0


def exercise(browser, url, app, clock, artifacts):
    errors = []
    player = 100
    for width, height in [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]:
        for scheme in ['dark', 'light']:
            for language in ['ru', 'en']:
                player += 1
                label = f'{width}x{height}-{scheme}-{language}'
                context, page = context_for(browser, url, clock, player, width, height, scheme, language)
                page.on('pageerror', lambda error: errors.append(str(error)))
                expect(page.locator('.roost-events')).to_contain_text('Здесь появятся бонусы' if language == 'ru' else 'Bonuses for friends')
                page.locator('.roost-events').evaluate("n => n.scrollIntoView({block:'end', behavior:'instant'})")
                page.screenshot(path=str(artifacts / f'{label}-events-empty.png'))
                seed_reward_events(app, page, clock, player)
                seed(app, page, passive_at=clock() - 3600, xp=1234567, battles=1234567890, pvp_wins=987654321)
                referral = page.locator('.roost-referral > .ui-muted')
                expect(referral).to_have_text(
                    'Ты получишь 300 монет, когда новый друг впервые зайдёт в игру по твоей ссылке, и ещё 300 монет после его 3 боёв. Учитываются бесплатный бой, тренировки и онлайн.'
                    if language == 'ru' else
                    'You receive 300 coins when a new friend first joins the game through your link, plus 300 more after they complete 3 battles. The free battle, training and online battles all count.'
                )
                layout(page, label)
                expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'ready')
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(artifacts / f'{label}-ready.png'), full_page=True)
                page.screenshot(path=str(artifacts / f'{label}-viewport.png'))
                result = command(page, '[data-action=claim-daily]', 'claim/daily')
                assert result['result']['payout_minor'] == 17000
                expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'claimed')
                expect(page.locator('[data-action=claim-daily]')).to_be_disabled()
                assert page.locator('[data-action=claim-daily].ui-primary').count() == 0
                expect(page.locator('[data-reward=daily] .roost-feedback')).to_be_visible()
                # No inferred countdown/date: the formatted timestamp comes from the server.
                date = page.evaluate("ts => new Intl.DateTimeFormat(document.documentElement.lang, {hour:'2-digit',minute:'2-digit',day:'numeric',month:'short'}).format(new Date(ts*1000))", result['state']['economy']['next_daily_at'])
                expect(page.locator('#roost-daily-description')).to_contain_text(date)
                command(page, '[data-action=claim-passive]', 'claim/passive')
                expect(page.locator('[data-reward=passive]')).to_have_attribute('data-state', 'claimed')
                layout(page, label + '-claimed')
                page.locator('[data-action=invite]').evaluate("n => n.scrollIntoView({block:'center'})")
                page.screenshot(path=str(artifacts / f'{label}-invite.png'))
                page.locator('.roost-events').evaluate("n => n.scrollIntoView({block:'end', behavior:'instant'})")
                page.screenshot(path=str(artifacts / f'{label}-events.png'))
                page.reload(wait_until='networkidle')
                expect(page.locator('.roost-event')).to_have_count(4)
                expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'waiting')
                expect(page.locator('[data-reward=passive]')).to_have_attribute('data-state', 'waiting')
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(artifacts / f'{label}-waiting.png'), full_page=True)
                context.close()
                print('Passed:', label, flush=True)

    context, page = context_for(browser, url, clock, 900)
    page.on('pageerror', lambda error: errors.append(str(error)))
    # Repeat the actual mutation after a lost response, retaining UUID and body.
    for kind in ['daily', 'passive']:
        seed(app, page, passive_at=clock() - 3600)
        before = server_state(page)
        held = []
        pattern = f'**/api/v1/claim/{kind}'
        page.route(pattern, lambda route: held.append(route))
        page.locator(f'[data-action=claim-{kind}]').click()
        reward = page.locator(f'[data-reward={kind}]')
        expect(reward).to_have_attribute('data-state', 'claiming')
        expect(reward.locator('.ui-button')).to_have_attribute('aria-busy', 'true')
        expect(reward.locator('.ui-button')).to_be_disabled()
        expect(page.locator('.roost-progress')).to_be_visible()
        expect(page.locator('.roost-progress [role=progressbar]')).to_have_attribute('aria-valuenow', str(before['player']['xp_in_level']))
        expect(page.locator('.roost-progress [role=progressbar]')).to_have_attribute('aria-valuemax', str(before['player']['xp_to_next']))
        page.screenshot(path=str(artifacts / f'{kind}-claiming.png'), full_page=True)
        route = held.pop()
        original = (route.request.post_data_json, route.request.headers['idempotency-key'])
        assert original[0] == {}
        result = route.fetch().json()
        route.abort()
        expect(reward).to_have_attribute('data-state', 'retry')
        expect(reward.locator('.roost-feedback.is-error')).to_be_visible()
        page.screenshot(path=str(artifacts / f'{kind}-retry.png'), full_page=True)
        page.unroute(pattern)
        # Language changes preserve the request and translate local recovery.
        page.locator('[data-language=en]').click()
        with page.expect_response(lambda response: response.url.endswith(f'/claim/{kind}')) as received:
            reward.locator('[data-action=roost-retry]').click()
        retry = received.value
        assert (retry.request.post_data_json, retry.request.headers['idempotency-key']) == original
        expect(reward).to_have_attribute('data-state', 'claimed')
        expect(reward.locator('.roost-feedback')).to_contain_text('coins added to your wallet')
        assert server_state(page)['player']['balance_minor'] == before['player']['balance_minor'] + result['result']['payout_minor']
        assert page.locator('.roost-screen .is-error').count() == 0

    # A definite rejection retains the numbers and offers a local retry.
    seed(app, page, daily_at=clock() - 86401)
    page.route('**/api/v1/claim/daily', lambda route: route.fulfill(status=409, json={'error': {'code': 'daily_not_ready', 'message': 'raw backend detail'}}))
    page.locator('[data-action=claim-daily]').click()
    expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'error')
    expect(page.locator('[data-reward=daily] button')).to_have_text('Retry')
    assert 'raw backend detail' not in page.locator('.roost-screen').inner_text()
    page.unroute('**/api/v1/claim/daily')
    command(page, '[data-action=claim-daily]', 'claim/daily')
    expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'claimed')

    clock.advance(86400)
    refresh(page)
    expect(page.locator('[data-reward=daily]')).to_have_attribute('data-state', 'ready')
    assert page.locator('[data-reward=daily] .roost-feedback').count() == 0

    # Huge server-provided amounts must wrap without reducing essential font sizes.
    def huge_state(route):
        payload = route.fetch().json()
        snapshot = payload.get('state', payload)
        snapshot['economy'].update(daily_reward_minor=987654321012345, passive_available_minor=987654321012345, daily_available=True)
        snapshot['reward_events'] = [{'id': 900001, 'reason': 'referral_signup', 'amount_minor': 987654321012345, 'created_at': clock(), 'friend_name': 'AnExtremelyLongFriendNameWithoutSpaces<script>safe</script>'}]
        route.fulfill(json=payload)
    page.route('**/api/v1/state', huge_state)
    # Resume also schedules a presence mutation; retain the same fixture in its state.
    page.route('**/api/v1/presence', huge_state)
    page.set_viewport_size({'width': 320, 'height': 568})
    page.evaluate('Telegram.WebApp.viewportStableHeight=innerHeight; shellEmit("viewportChanged", {isStateStable:true})')
    refresh(page)
    # Waiting for the HTTP response alone can leave the previous DOM in place.
    # Check the actual fixture amount before retaining handles for layout checks.
    expect(page.locator('[data-reward=daily] .roost-amount')).to_contain_text('9,876,543,210,123.45')
    layout(page, 'large-values')
    expect(page.locator('[data-reward=daily] .roost-amount')).to_contain_text('9,876,543,210,123.45')
    page.screenshot(path=str(artifacts / 'large-values.png'), full_page=True)
    page.locator('.roost-events').evaluate("n => n.scrollIntoView({block:'center', behavior:'instant'})")
    page.screenshot(path=str(artifacts / 'large-values-events.png'))
    page.wait_for_load_state('networkidle')
    # Drain asynchronous fetch/fulfill work before restoring live responses.
    # These are the only page routes; the Telegram bridge is context-owned.
    page.unroute_all(behavior='wait')
    refresh(page)

    # Exact invitation contract: Telegram share URL and clipboard use the same referral.
    state = server_state(page)
    expected = f"https://t.me/roost_test_bot?startapp={state['player']['referral_code']}"
    page.locator('[data-action=invite]').click()
    shared = page.evaluate('sharedLinks.at(-1)')
    assert shared.startswith('https://t.me/share/url?')
    assert parse_qs(urlparse(shared).query) == {'url': [expected], 'text': ['Join me in Rooster Club. Let’s raise champions!']}
    page.evaluate('''Object.defineProperty(navigator, 'clipboard', {configurable:true, value: {
        writeText: text => new Promise(resolve => {window.copiedLink=text; window.finishCopy=resolve;})
    }})''')
    page.locator('[data-action=copy-invite]').click()
    expect(page.locator('[data-action=copy-invite]')).to_be_disabled()
    expect(page.locator('[data-action=copy-invite]')).to_have_text('Copying…')
    assert page.evaluate('copiedLink') == expected
    page.evaluate('finishCopy()')
    expect(page.locator('.roost-referral .roost-feedback')).to_contain_text('copied')
    page.evaluate("() => { navigator.clipboard.writeText = async () => {throw new Error('denied')}; }")
    page.locator('[data-action=copy-invite]').click()
    expect(page.locator('.roost-referral .is-error')).to_be_visible()
    expect(page.locator('[data-action=copy-invite]')).to_be_enabled()
    page.evaluate("() => { Telegram.WebApp.openTelegramLink = () => {throw new Error('bridge')}; }")
    page.locator('[data-action=invite]').click()
    expect(page.locator('.roost-referral .is-error')).to_contain_text('Could not open Telegram')
    page.screenshot(path=str(artifacts / 'invite-error.png'), full_page=True)
    page.evaluate('''() => {
        delete Telegram.WebApp.openTelegramLink;
        window.open = (...args) => { window.browserShare = args; };
    }''')
    page.locator('[data-action=invite]').click()
    assert page.evaluate('browserShare') == [shared, '_blank', 'noopener,noreferrer']
    page.route('**/api/v1/config', lambda route: route.fulfill(json={**route.fetch().json(), 'bot_username': ''}))
    page.reload(wait_until='networkidle')
    expect(page.locator('.roost-referral')).to_contain_text('Ссылка появится после настройки бота')
    assert page.locator('[data-action=invite], [data-action=copy-invite]').count() == 0
    context.close()
    assert not errors, errors
    print('Passed: actual claims, loading, safe retries, errors, huge values, share/clipboard contract', flush=True)


def main():
    artifacts = ROOT / 'artifacts' / 'roost-layout'
    artifacts.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    with TemporaryDirectory(prefix='roosters-roost-') as temporary:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, bot_token=TOKEN, bot_username='roost_test_bot', secret_key='test' * 16, database_path=str(Path(temporary) / 'game.sqlite3')), clock=clock)
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
    print('Roost checks passed: 20 viewport/theme/language combinations with simulated Telegram safe areas')


if __name__ == '__main__':
    main()
