"""Visual/interaction audit of the static Arena preview; no app, auth or database.

Start: .venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory web
Run: .venv/bin/python -m scripts.check_arena_preview [--first]
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
SIZES = [(390, 844), (360, 640), (360, 560), (320, 568), (430, 932)]


def geometry(page):
    return page.evaluate('''() => {
      const rect = selector => {
        const r = document.querySelector(selector).getBoundingClientRect();
        return {x:r.x, y:r.y, width:r.width, height:r.height, bottom:r.bottom};
      };
      return {fight:rect('#ap-fight'), nav:rect('.ap-nav'), scroll:rect('.ap-scroll'),
        nameplate:rect('.ap-fighter-info h1'), header:rect('.ap-header'),
        overflow:document.querySelector('.ap-scroll').scrollWidth > innerWidth,
        targets:[...document.querySelectorAll('button,summary')].every(n=>n.getBoundingClientRect().height>=44),
        loaded:document.querySelector('.ap-hero').naturalWidth,
        font:document.fonts.check('1000 24px "Arena Nunito"')};
    }''')


def capture(page, folder, label):
    page.screenshot(path=str(folder / f'{label}.png'), scale='css')


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--first', action='store_true')
    args.add_argument('--viewport', help='Recheck only one viewport, e.g. 360x640')
    args.add_argument('--url', default='http://127.0.0.1:8765/arena-preview/')
    opts = args.parse_args()
    folder = ROOT / 'artifacts/arena-preview'
    folder.mkdir(parents=True, exist_ok=True)
    report, errors, requests = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
        sizes = SIZES[:1] if opts.first else SIZES
        if opts.viewport:
            sizes = [tuple(map(int, opts.viewport.split('x')))]
        for width, height in sizes:
            for lang in (['ru'] if opts.first else ['ru', 'en']):
                for scheme in (['dark'] if opts.first else ['dark', 'light']):
                    context = browser.new_context(viewport={'width':width,'height':height}, device_scale_factor=2, color_scheme=scheme, reduced_motion='reduce')
                    page = context.new_page()
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('request', lambda r: requests.append({'method':r.method,'url':r.url}))
                    label = f'{width}x{height}-{lang}-{scheme}'
                    page.goto(opts.url + '?lang=' + lang, wait_until='networkidle')
                    page.evaluate('document.fonts.ready')
                    data = geometry(page)
                    assert not data['overflow'], (label, data)
                    assert data['targets'] and data['loaded'] == 1254 and data['font'], (label, data)
                    assert data['scroll']['bottom'] <= data['nav']['y'] + 1, (label, data)
                    assert data['nameplate']['y'] >= data['header']['bottom'] - 2, (label, data)
                    if height >= 640:
                        assert data['fight']['bottom'] <= data['nav']['y'], (label, data)
                    capture(page, folder, label)
                    page.locator('#ap-fight').scroll_into_view_if_needed()
                    visible = geometry(page)
                    assert visible['fight']['bottom'] <= visible['nav']['y'], (label, visible)
                    assert visible['fight']['y'] >= 0
                    capture(page, folder, label + '-fight')
                    page.locator('#ap-rules summary').click()
                    expect(page.locator('#ap-rules')).to_have_attribute('open', '')
                    page.locator('.ap-history-row').scroll_into_view_if_needed()
                    capture(page, folder, label + '-support')
                    report.append({'label':label, **data})
                    context.close()
        if not opts.first and not opts.viewport:
            context = browser.new_context(viewport={'width':390,'height':844}, device_scale_factor=2)
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: requests.append({'method':r.method,'url':r.url}))
            for lang in ['ru','en']:
                for scene in ['loading','unavailable','noquote','error','empty','victory','first','long']:
                    page.goto(opts.url + f'?lang={lang}&state={scene}', wait_until='networkidle')
                    assert not geometry(page)['overflow'], (lang,scene)
                    if scene in ['loading','unavailable','noquote']:
                        expect(page.locator('#ap-fight')).to_be_disabled()
                    capture(page,folder,f'390x844-{lang}-{scene}')
            page.goto(opts.url,wait_until='networkidle')
            wallet = page.locator('.ap-wallet').inner_text()
            result = page.locator('.ap-result').inner_text()
            page.locator('#ap-mode-online').hover()
            page.mouse.down()
            page.wait_for_timeout(150)
            capture(page,folder,'390x844-ru-pressed')
            page.mouse.up()
            expect(page.locator('#ap-mode-online')).to_have_attribute('aria-pressed','true')
            page.locator('#ap-stake-2500').click()
            expect(page.locator('#ap-stake-2500')).to_have_attribute('aria-pressed','true')
            expect(page.locator('.ap-risk')).to_contain_text('45')
            page.locator('#ap-fight').click()
            expect(page.locator('#ap-fight')).to_have_attribute('aria-busy','true')
            expect(page.locator('#ap-stake-5000')).to_be_disabled()
            expect(page.locator('#ap-fight')).to_have_attribute('aria-busy','false',timeout=3000)
            assert page.locator('.ap-wallet').inner_text() == wallet
            assert page.locator('.ap-result').inner_text() == result
            expect(page.locator('#ap-notice')).to_be_visible()
            page.locator('#ap-lang-en').click()
            expect(page.locator('html')).to_have_attribute('lang','en')
            expect(page.locator('#ap-stake-2500')).to_have_attribute('aria-pressed','true')
            page.keyboard.press('Tab')
            capture(page,folder,'390x844-en-keyboard-focus')
            for tab in ['gear','roost','leaderboard']:
                page.locator('#ap-tab-' + tab).click(force=True)
                expect(page.locator('#ap-nav-notice')).to_contain_text('Only Arena')
            # Verify the independent browser-level prohibition on network commands.
            assert page.evaluate('''async () => {
                try { await fetch('/api/v1/preview-must-never-reach-server', {method:'POST'}); return false; }
                catch { return true; }
            }''')
            context.close()
            # Native geometry is simulated; this does not claim physical-device QA.
            for width, height in [(390,844),(360,560)]:
                for scheme in ['dark','light']:
                    context = browser.new_context(viewport={'width':width,'height':height},device_scale_factor=2,reduced_motion='reduce')
                    context.add_init_script('''
                      const events = {};
                      window.Telegram = {WebApp:{platform:'ios',version:'8.0',
                        colorScheme:SCHEME,themeParams:{},viewportStableHeight:innerHeight,
                        safeAreaInset:{top:24,bottom:20,left:4,right:6},
                        contentSafeAreaInset:{top:16,bottom:10,left:2,right:2},
                        isVersionAtLeast:()=>true,onEvent:(n,f)=>{events[n]=f;},ready(){},expand(){}}};
                      window.previewTheme = s => {Telegram.WebApp.colorScheme=s;events.themeChanged();};
                    '''.replace('SCHEME',json.dumps(scheme)))
                    page = context.new_page()
                    page.goto(opts.url,wait_until='networkidle')
                    data = geometry(page)
                    assert data['scroll']['bottom'] <= data['nav']['y'] + 1
                    assert page.locator('.ap-header').bounding_box()['y'] == 40
                    page.locator('#ap-fight').scroll_into_view_if_needed()
                    assert geometry(page)['fight']['bottom'] <= data['nav']['y']
                    page.locator('#ap-stake-2500').click()
                    page.evaluate('previewTheme("%s")' % ('light' if scheme=='dark' else 'dark'))
                    expect(page.locator('#ap-stake-2500')).to_have_attribute('aria-pressed','true')
                    capture(page,folder,f'{width}x{height}-safe-{scheme}-fight')
                    context.close()
        assert not errors, errors
        assert not [r for r in requests if r['method'] != 'GET' or '/api/' in r['url']], requests
        browser.close()
    report_name = 'first-report.json' if opts.first else f'report-{opts.viewport}.json' if opts.viewport else 'report.json'
    (folder / report_name).write_text(json.dumps({'matrix':report,'page_errors':errors,'requests':requests},indent=2))
    print(json.dumps({'passed':len(report),'artifacts':str(folder),'page_errors':errors}))


if __name__ == '__main__':
    main()
