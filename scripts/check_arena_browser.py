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
ARENA_ART = ('arena-background.webp', 'rooster-hero.webp')


def check_arena_art(page, label):
    """Both live Arena entry states render the approved art and readable live data."""
    scene = page.locator('.arena-scene')
    expect(scene).to_have_count(1)
    if page.locator('.first-fight').count():
        # Keep onboarding's free action first; inspect its scene after a normal scroll.
        scene.evaluate("scene => scene.scrollIntoView({block: 'center', behavior: 'instant'})")
    for kind, filename in zip(('background', 'hero'), ARENA_ART):
        image = scene.locator(f'img.arena-scene-{kind}')
        expect(image).to_have_count(1)
        if kind == 'background':
            expect(image).to_have_attribute('alt', '')
            expect(image).to_have_attribute('aria-hidden', 'true')
        else:
            assert (image.get_attribute('alt') or '').strip(), label
        expect(image).to_be_visible()
        page.wait_for_function('''selector => {
          const image = document.querySelector(selector);
          return image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0;
        }''', arg=f'.arena-scene-{kind}')
        assert image.evaluate('image => new URL(image.currentSrc).pathname') == f'/static/assets/arena/{filename}', label
    data = scene.evaluate('''scene => {
      const rect = node => node.getBoundingClientRect().toJSON();
      const hero = scene.querySelector('.arena-scene-hero');
      const identity = scene.querySelector('.arena-fighter-identity');
      const copy = [...identity.querySelectorAll('h1, p, .arena-xp > span')].map(node => {
        const range = document.createRange();
        range.selectNodeContents(node);
        return {text: node.textContent, box: rect(node), textBox: range.getBoundingClientRect().toJSON(),
          fontSize: parseFloat(getComputedStyle(node).fontSize),
          clipped: node.scrollWidth > node.clientWidth + 1 || node.scrollHeight > node.clientHeight + 1};
      });
      return {scene: rect(scene), hero: rect(hero), identity: rect(identity), copy,
        heroFit: getComputedStyle(hero).objectFit,
        scrollY, viewport: {width: innerWidth, height: innerHeight}};
    }''')
    box, hero = data['scene'], data['hero']
    assert box['width'] > 0 and box['height'] >= 120, (label, data)
    assert 0 <= box['left'] < box['right'] <= data['viewport']['width'] + 1, (label, data)
    assert box['top'] >= 0 and box['bottom'] <= data['viewport']['height'] + 1, (label, data)
    assert data['heroFit'] == 'contain' and hero['height'] >= 100 and hero['width'] >= 80, (label, data)
    assert (hero['left'] >= box['left'] - 1 and hero['right'] <= box['right'] + 1
            and hero['top'] >= box['top'] - 1 and hero['bottom'] <= box['bottom'] + 1), (label, data)
    assert len(data['copy']) >= 3, (label, data)
    for item in data['copy']:
        text_box = item['textBox']
        assert item['text'].strip() and item['fontSize'] >= 12 and not item['clipped'], (label, item)
        assert (text_box['left'] >= box['left'] - 1 and text_box['right'] <= box['right'] + 1
                and text_box['top'] >= box['top'] - 1 and text_box['bottom'] <= box['bottom'] + 1), (label, item, box)
    state = server_state(page)['player']
    progress = scene.locator('[role=progressbar]')
    expect(progress).to_have_attribute('aria-valuenow', str(state['xp_in_level']))
    expect(progress).to_have_attribute('aria-valuemax', str(state['xp_to_next']))
    return data


def snapshot(page, artifacts, label):
    page.evaluate('window.scrollTo(0, 0)')
    check_layout(page, label)
    art = check_arena_art(page, label) if page.locator('.first-fight, .arena-play').count() else None
    if art and page.locator('.first-fight').count():
        page.screenshot(path=str(artifacts / (label + '-scene-viewport.png')))
        page.evaluate('window.scrollTo(0, 0)')
    # Arena, queue and history must consume the same illustrated foundation.
    assert page.locator('.app-shell.ui-shell').count() == 1, label
    assert page.locator('#main button:not(.ui-button)').count() == 0, label
    assert page.locator('#main .ui-title, #main .ui-headline').count() > 0, label
    assert page.locator('#main .ui-panel').count() > 0, label
    assert page.locator('.app-shell').evaluate("el => getComputedStyle(el).backgroundColor") == 'rgb(7, 92, 219)', label
    page.screenshot(path=str(artifacts / (label + '.png')), full_page=True)
    page.screenshot(path=str(artifacts / (label + '-viewport.png')))
    geometry = page.evaluate('''() => {
      const rect = s => document.querySelector(s)?.getBoundingClientRect().toJSON();
      return {fight: rect('.arena-fight'), nav: rect('#navigation'),
        risk: rect('.arena-risk'), fighter: rect('.arena-fighter')};
    }''')
    return {**geometry, 'art': art}


def stake_text(page, minor):
    return page.evaluate('''minor => new Intl.NumberFormat(
        document.documentElement.lang === 'ru' ? 'ru-RU' : 'en-US',
        {maximumFractionDigits: 2}).format(minor / 100)''', minor)


def assert_stake_display(page, minor):
    expect(page.locator('#arena-stake-range')).to_have_value(str(minor))
    expect(page.locator('#arena-stake-value')).to_have_text(stake_text(page, minor))


def exercise_slider(page, label, *, first_free=False):
    """Actual pointer/keyboard input changes only the selected, exact-cent stake."""
    slider = page.locator('#arena-stake-range')
    balance = server_state(page)['player']['balance_minor']
    expect(slider).to_have_attribute('min', '1000')
    expect(slider).to_have_attribute('max', str(balance))
    expect(slider).to_have_attribute('step', '1')
    expect(slider).to_be_enabled()
    slider.scroll_into_view_if_needed()
    geometry = page.evaluate('''() => {
        const node = document.querySelector('#arena-stake-range');
        node.scrollIntoView({block:'center', behavior:'instant'});
        const box = node.getBoundingClientRect();
        return {box: box.toJSON(), named: Boolean(node.labels?.length || node.getAttribute('aria-label')),
            reachable: [0.1,0.5,0.9].every(x => document.elementFromPoint(
                box.x + box.width * x, box.y + box.height / 2) === node)};
    }''')
    assert geometry['box']['height'] >= 44 and geometry['named'] and geometry['reachable'], (label, geometry)
    before_balance = page.locator('#balance').inner_text()
    mutations = []

    def record(request):
        if request.method == 'POST' and '/api/v1/' in request.url and not request.url.endswith('/presence'):
            mutations.append(request.url)

    page.on('request', record)
    try:
        slider.focus()
        page.keyboard.press('Home')
        assert_stake_display(page, 1000)
        page.evaluate('window.savedStakeRange = document.querySelector("#arena-stake-range")')
        box = slider.bounding_box()
        page.mouse.move(box['x'] + 12, box['y'] + box['height'] / 2)
        page.mouse.down()
        previous = 1000
        try:
            for ratio in (.25, .55, .85):
                page.mouse.move(box['x'] + box['width'] * ratio, box['y'] + box['height'] / 2, steps=4)
                page.wait_for_function('previous => Number(document.querySelector("#arena-stake-range").value) > previous', arg=previous)
                previous = int(slider.input_value())
                assert_stake_display(page, previous)
                assert page.evaluate('savedStakeRange === document.querySelector("#arena-stake-range")'), label
                if ratio == .55:
                    refresh(page)
                    assert page.evaluate('savedStakeRange === document.querySelector("#arena-stake-range")'), label + ': poll replaced a dragging range'
        finally:
            page.mouse.up()
        slider.focus()
        page.keyboard.press('End')
        assert_stake_display(page, balance)
        action = '[data-action=queue-join]' if first_free else '.arena-fight'
        expect(page.locator(action)).to_be_enabled()
        expect(page.locator(action)).to_contain_text(stake_text(page, balance))
        page.keyboard.press('Home')
        assert_stake_display(page, 1000)
        page.locator('[data-action=stake][data-stake-minor="2500"]').click()
        assert_stake_display(page, 2500)
        expect(page.locator('[data-action=stake][data-stake-minor="2500"]')).to_have_attribute('aria-pressed', 'true')
        slider.focus()
        page.keyboard.press('ArrowRight')
        assert_stake_display(page, 2501)
        expect(page.locator('[data-action=stake][aria-pressed=true]')).to_have_count(0)
        expect(page.locator(action)).to_be_enabled()
        expect(page.locator(action)).to_contain_text(stake_text(page, 2501))
        page.locator('[data-action=stake][data-stake-minor="1000"]').click()
        assert_stake_display(page, 1000)
        expect(page.locator(action)).to_be_enabled()
        assert page.locator('#balance').inner_text() == before_balance, label
        assert not mutations, (label, mutations)
    finally:
        page.remove_listener('request', record)
    page.evaluate('window.scrollTo(0, 0)')


def exercise_help(page, mode, artifacts, label, *, first_free=False):
    """Mode information is reachable without changing or submitting a wager."""
    trigger = page.locator(f'[data-action=mode-help][data-mode={mode}]')
    panel = page.locator('#arena-mode-help')
    expect(panel).to_be_hidden()
    expect(trigger).to_have_attribute('aria-expanded', 'false')
    expect(trigger).to_have_attribute('aria-controls', 'arena-mode-help')
    trigger.scroll_into_view_if_needed()
    box = trigger.bounding_box()
    assert box['width'] >= 44 and box['height'] >= 44, (label, box)
    if not first_free:
        mode_box = page.locator(f'[data-action=arena-mode][data-mode={mode}]').bounding_box()
        assert box['x'] + box['width'] / 2 >= mode_box['x'] + mode_box['width'] / 2, (label, box, mode_box)
        assert abs(box['y'] - mode_box['y']) <= 8, (label, box, mode_box)

    def selection():
        return page.evaluate('''() => ({
          mode: document.querySelector('[data-action=arena-mode][aria-pressed=true]')?.dataset.mode ?? null,
          stake: document.querySelector('[data-action=stake][aria-pressed=true]')?.dataset.stakeMinor,
          range: document.querySelector('#arena-stake-range')?.value,
          balance: document.querySelector('#balance').textContent,
          pending: sessionStorage.getItem('rooster.v1.pending')
        })''')

    before = selection()
    mutations = []

    def record(request):
        if (request.method == 'POST' and '/api/v1/' in request.url
                and not request.url.endswith(('/presence', '/auth'))):
            mutations.append(request.url)

    page.on('request', record)
    try:
        trigger.click()
        expect(panel).to_be_visible()
        expect(trigger).to_have_attribute('aria-expanded', 'true')
        assert selection() == before, label + ': help changed the selected fight'
        assert panel.locator('.arena-risk').count() == 1, label
        assert panel.locator('.arena-risk').evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 14
        if mode == 'bot':
            expect(panel).to_contain_text('110')
        snapshot(page, artifacts, label + '-help-' + mode)
        # The same question toggles the disclosure without selecting that mode.
        trigger.click()
        expect(panel).to_be_hidden()
        expect(trigger).to_have_attribute('aria-expanded', 'false')
        trigger.click()
        expect(panel).to_be_visible()
        page.keyboard.press('Escape')
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()
        trigger.click()
        panel.locator('[data-action=close-mode-help]').click()
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()
        assert selection() == before, label + ': help changed the selected fight'
        assert not mutations, (label, mutations)
    finally:
        page.remove_listener('request', record)
    page.evaluate('window.scrollTo(0, 0)')


def exercise_art_variants(page, artifacts, width, height):
    """Visual response fixtures only; no commands run with invented test values."""
    names = {'ru': 'Северный чемпион арены', 'en': 'Northern Arena Champion'}
    variant = {'name': names['ru']}

    def large_fighter(route):
        payload = route.fetch().json()
        state = payload.get('state', payload)
        state['catalog']['breeds'].append({**state['catalog']['breeds'][0],
            'id': 'visual-fixture', 'name': variant['name']})
        state['player'].update(breed_id='visual-fixture', level=123, power=999999,
            xp_in_level=999999, xp_to_next=1000000)
        route.fulfill(json=payload)

    page.route('**/api/v1/state', large_fighter)
    page.route('**/api/v1/presence', large_fighter)
    try:
        for language in ('ru', 'en'):
            variant['name'] = names[language]
            page.locator(f'[data-language={language}]').click()
            refresh(page)
            expect(page.locator('.arena-fighter-identity h1')).to_have_text(variant['name'])
            for theme in ('dark', 'light'):
                page.evaluate(f"battleTheme('{theme}')")
                snapshot(page, artifacts, f'long-fighter-{width}x{height}-{language}-{theme}')
    finally:
        page.unroute_all(behavior='wait')


def exercise(browser, url, clock, artifacts):
    errors, matrix = [], {}
    for filename in ARENA_ART:
        assert (ROOT / 'web/assets/arena' / filename).read_bytes() == (ROOT / 'docs/design/assets' / filename).read_bytes(), filename
    for width, height in [(360, 640), (390, 844), (430, 932), (360, 560), (320, 568)]:
        context = browser.new_context(viewport={'width': width, 'height': height})
        context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(
            status=200, content_type='application/javascript', body=BRIDGE))
        context.add_init_script('const snapshotTime = Date.now(); Date.now = () => snapshotTime;')
        context.add_init_script("localStorage.setItem('rooster.v1.identity', JSON.stringify(%s))" % json.dumps(
            {'user_id': f'arena_{width}_{height}', 'name': 'Боец с длинным именем'}))
        context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        if width == 360 and height == 640:
            for filename in ARENA_ART:
                response = page.request.get(f'{url}/static/assets/arena/{filename}')
                assert response.ok and response.body() == (ROOT / 'docs/design/assets' / filename).read_bytes(), filename
        for language in ('ru', 'en'):
            page.locator(f'[data-language="{language}"]').click()
            for theme in ('dark', 'light'):
                page.evaluate(f"battleTheme('{theme}')")
                expect(page.locator('[data-action=start-free]')).to_be_visible()
                assert page.locator('.hero').count() == 1
                label = f'first-{width}x{height}-{language}-{theme}'
                matrix[label] = snapshot(page, artifacts, label)
                exercise_slider(page, label, first_free=True)
                exercise_help(page, 'online', artifacts, label, first_free=True)
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
                    expect(page.locator('[data-action=arena-mode][data-mode=bot]')).to_contain_text(
                        'Тренировка' if language == 'ru' else 'Training')
                    assert page.locator('.arena-setup .primary').count() == 1
                    assert geometry['fight']['height'] >= 48
                    # Tall phones fit the full setup; short phones allow natural
                    # scrolling while the entire action remains reachable.
                    if width >= 360 and height >= 800:
                        assert geometry['fight']['bottom'] <= geometry['nav']['top'], (label, geometry)
                        assert page.locator('.arena-fight').evaluate('''button => {
                          const r = button.getBoundingClientRect();
                          return button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
                        }'''), label
                    else:
                        # The narrow RU setup wraps naturally. A short normal
                        # scroll must expose the complete action above the nav.
                        page.evaluate('''() => {
                          const button = document.querySelector('.arena-fight').getBoundingClientRect();
                          const nav = document.querySelector('#navigation').getBoundingClientRect();
                          window.scrollBy(0, Math.max(0, button.bottom - nav.top + 12));
                        }''')
                        assert page.locator('.arena-fight').evaluate('''button => {
                          const r = button.getBoundingClientRect();
                          const nav = document.querySelector('#navigation').getBoundingClientRect();
                          return r.bottom <= nav.top && button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
                        }'''), label
                        page.evaluate('window.scrollTo(0, 0)')
                    expect(page.locator('.last-battle-card, #last-result')).to_have_count(0)
                    expect(page.locator('#arena-mode-help')).to_be_hidden()
                    if mode == 'bot':
                        exercise_slider(page, label)
                    # Both questions work, including the unselected mode's help.
                    for help_mode in ('bot', 'online'):
                        exercise_help(page, help_mode, artifacts, label)
        # Real stake changes, reload, queue and cancellation preserve selections.
        page.locator('[data-action=mode-help][data-mode=online]').click()
        page.locator('[data-action=stake][data-stake-minor="2500"]').click()
        expect(page.locator('#arena-mode-help .arena-risk')).to_contain_text('47.5')
        refresh(page)
        expect(page.locator('#arena-mode-help')).to_be_visible()
        expect(page.locator('#arena-mode-help .arena-risk')).to_contain_text('47.5')
        page.locator('[data-language=ru]').click()
        expect(page.locator('#arena-mode-help')).to_be_visible()
        expect(page.locator('#arena-mode-help .arena-risk')).to_contain_text('47,5')
        page.locator('[data-language=en]').click()
        page.locator('[data-action=arena-mode][data-mode=bot]').click()
        expect(page.locator('#arena-mode-help')).to_be_hidden()
        page.locator('[data-action=arena-mode][data-mode=online]').click()
        page.reload(wait_until='networkidle')
        expect(page.locator('[data-action=arena-mode][data-mode=online]')).to_have_attribute('aria-pressed', 'true')
        expect(page.locator('#arena-mode-help')).to_be_hidden()
        expect(page.locator('[data-stake-minor="2500"]')).to_have_attribute('aria-pressed', 'true')
        before = server_state(page)['player']['balance_minor']
        queued = command(page, '[data-action=queue-join]', 'queue/join')
        assert queued['state']['player']['balance_minor'] == before
        for language in ('ru', 'en'):
            page.locator(f'[data-language="{language}"]').click()
            for theme in ('dark', 'light'):
                page.evaluate(f"battleTheme('{theme}')")
                label = f'queue-{width}x{height}-{language}-{theme}'
                matrix[label] = snapshot(page, artifacts, label)
                expect(page.locator('[data-action=queue-leave].ui-button')).to_be_enabled()
                assert page.locator('.queue-card .radar').evaluate("el => getComputedStyle(el, '::after').content") == 'none'
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
            for language in ('ru', 'en'):
                page.locator(f'[data-language={language}]').click()
                for theme in ('dark', 'light'):
                    page.evaluate(f"battleTheme('{theme}')")
                    exercise_slider(page, f'safe-areas-{language}-{theme}')
                    for mode in ('bot', 'online'):
                        exercise_help(page, mode, artifacts, f'safe-areas-{language}-{theme}')
            page.locator('.arena-fight').scroll_into_view_if_needed()
            page.evaluate('document.documentElement.removeAttribute("style")')
            # Unaffordable presets cannot bypass the balance limit.
            page.locator('[data-tab=gear]').click()
            for _ in range(2):
                command(page, '[data-action=upgrade][data-slot=sword]', 'gear/upgrade')
            page.locator('[data-tab=arena]').click()
            expect(page.locator('[data-action=stake][data-stake-minor="10000"]')).to_be_disabled()
            remaining = server_state(page)['player']['balance_minor']
            expect(page.locator('#arena-stake-range')).to_have_attribute('max', str(remaining))
            expect(page.locator('.arena-fight')).to_be_enabled()
            snapshot(page, artifacts, 'returning-disabled-expensive-presets')
            page.locator('[data-action=stake][data-stake-minor="1000"]').click()
            expect(page.locator('.arena-fight')).to_be_enabled()
        if width in (320, 390):
            exercise_art_variants(page, artifacts, width, height)
        context.close()
    assert not errors, errors
    (artifacts / 'matrix.json').write_text(json.dumps(matrix, indent=2))
    print('Arena matrix passed:', len(matrix), 'screens + 8 long-fighter fixtures; approved live art loading and containment, live XP, native stake drag/keyboard/presets/polling, question controls, disclosure accessibility, no mutation, real queue/reload/selection and funds checks.')
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
