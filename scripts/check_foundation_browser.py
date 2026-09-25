"""Rendered Wave 1 component and production-navigation audit.

Run: python -m scripts.check_foundation_browser. Uses a disposable database and
test-only component HTML; it does not add a product route or mutate game state.
Native insets/theme events use a simulated bridge, not a physical Telegram app.
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
from scripts.check_browser import Clock, QuietRequests, check_layout, server_state
from scripts.check_shell_browser import BRIDGE

ROOT = Path(__file__).resolve().parent.parent
SIZES = [(320, 568), (360, 560), (360, 640), (390, 844), (430, 932)]

# Only the audit layout is synthetic. The components, icons, font and Telegram
# environment below are the exact assets used by the application and preview.
FIXTURE = """<!doctype html><html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#075cdb"><title>Foundation audit fixture</title>
<link rel="stylesheet" href="/static/styles.css">
<link rel="stylesheet" href="/static/rankings.css">
<link rel="stylesheet" href="/static/foundation.css">
<style>
body{background:var(--ui-bg,#075cdb);color:var(--ui-text)}
.audit-wrapper{max-width:460px;margin:auto;padding:calc(16px + var(--app-inset-top)) calc(16px + var(--app-inset-right)) calc(24px + var(--app-inset-bottom)) calc(16px + var(--app-inset-left));display:grid;gap:16px}
.audit-wrapper>section{min-width:0}.audit-wrapper>section:not(.ui-info):not(.audit-icons){display:grid;gap:12px}
.audit-chips{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
.audit-wrapper>.audit-icons{display:flex;flex-wrap:wrap;gap:12px}.audit-icons .ui-icon{width:32px;height:32px}
.audit-copy{font-size:15px;line-height:1.4;margin:0}.audit-muted{color:var(--ui-muted)}
.audit-wrapper h1,.audit-wrapper h2{margin:0}.audit-wrapper .ui-panel,.audit-wrapper .ui-info,.audit-wrapper .ui-status,.audit-wrapper .ui-result{padding:12px}
</style></head><body><main class="audit-wrapper" id="fixture"></main>
<script type="module">
import {createAppEnvironment} from '/static/environment.mjs';
import {icon} from '/static/icons.mjs';
const environment=createAppEnvironment();environment.start();
const en=new URLSearchParams(location.search).get('lang')==='en';
document.documentElement.lang=en?'en':'ru';
const copy=en?{
 title:'Shared components',section:'Choose a battle',primary:'Enter battle',secondary:'Try again',
 bot:'Training',online:'Online duel',loading:'Confirming…',disabled:'Unavailable',
 info:'Only in-game coins. Your selection is preserved while loading.',
 success:'Reward confirmed',error:'Connection lost. Try again.',result:'Last battle',
 long:'A very long localized explanation stays readable at the narrowest viewport.'
}:{title:'Общие компоненты',section:'Выбери бой',primary:'В бой',secondary:'Повторить',
 bot:'Тренировка',online:'Онлайн-дуэль',loading:'Подтверждаем…',disabled:'Недоступно',
 info:'Только игровые монеты. Выбор сохраняется во время ожидания.',
 success:'Награда подтверждена',error:'Связь потеряна. Повтори попытку.',result:'Последний бой',
 long:'Очень длинное локализованное пояснение остаётся читаемым на самом узком экране.'};
document.querySelector('#fixture').innerHTML=`
 <h1 class="ui-title">${copy.title}</h1>
 <section><h2 class="ui-title">${copy.section}</h2>
 <div class="ui-segmented" role="group" aria-label="${copy.section}">
  <button class="ui-button" aria-pressed="true" id="selected">${icon('bot')}<span class="ui-selection">${icon('check')}</span>${copy.bot}</button>
  <button class="ui-button ui-accent" aria-pressed="false" id="unselected">${icon('arena')}${copy.online}</button>
 </div>
 <div class="audit-chips" role="group" aria-label="${en?'Selection':'Выбор'}">${[10,25,50,100].map(value=>`<button class="ui-button ui-chip" aria-pressed="${value===50}"><span class="ui-selection">${icon('check')}</span>${value}${icon('coin')}</button>`).join('')}</div>
 <button class="ui-button ui-primary" id="primary">${copy.primary}${icon('arrow-up-right')}</button>
 <button class="ui-button ui-secondary" id="secondary">${copy.secondary}</button>
 <button class="ui-button ui-primary" id="loading" disabled aria-busy="true"><span class="ui-spinner" aria-hidden="true"></span>${copy.loading}</button>
 <button class="ui-button" id="disabled" disabled>${copy.disabled}</button>
 <button class="ui-button ui-chip" id="selected-disabled" disabled aria-pressed="true"><span class="ui-selection">${icon('check')}</span>${copy.disabled}</button></section>
 <section class="ui-panel" id="panel"><p class="audit-copy">${copy.long}</p><p class="audit-copy audit-muted">${copy.info}</p>
 <div class="ui-progress" role="progressbar" aria-label="XP" aria-valuemin="0" aria-valuemax="100" aria-valuenow="60"><span style="width:60%"></span></div><span>60 / 100 XP</span></section>
 <section class="ui-info" id="info">${icon('info')}<p class="audit-copy">${copy.info}</p></section>
 <section class="ui-status" data-tone="success" role="status" id="success"><p class="audit-copy">${icon('check')}${copy.success}</p></section>
 <section class="ui-status" data-tone="error" role="status" id="error"><p class="audit-copy">${icon('alert')}${copy.error}</p></section>
 <section class="ui-result" data-tone="error" id="result"><h2 class="ui-title">${copy.result}</h2><p class="audit-copy">${copy.error}</p></section>
 <section class="ui-panel audit-icons" aria-label="Icons">${['coin','bot','arena','gear','roost','trophy','check','close','info','document','refresh','alert'].map(name=>icon(name,name)).join('')}</section>`;
window.fixtureReady=true;
</script></body></html>"""
FIXTURE_HEAD, FIXTURE_SCRIPT = FIXTURE.split('<script type="module">', 1)
FIXTURE_SCRIPT, FIXTURE_TAIL = FIXTURE_SCRIPT.split('</script>', 1)
FIXTURE_HTML = FIXTURE_HEAD + '<script type="module" src="/__foundation_fixture__.mjs"></script>' + FIXTURE_TAIL


def component_geometry(page, label):
    check_layout(page, label)
    data = page.evaluate("""() => {
      const style=id=>getComputedStyle(document.getElementById(id));
      const targets=[...document.querySelectorAll('button')].map(n=>({id:n.id,height:n.getBoundingClientRect().height}));
      const track=document.querySelector('.ui-progress'), fill=track.firstElementChild;
      return {targets,selected:style('selected').backgroundImage,unselected:style('unselected').backgroundImage,
        disabledOpacity:style('disabled').opacity, disabledFill:style('disabled').backgroundImage,
        primaryFill:style('primary').backgroundImage, progress:fill.getBoundingClientRect().width/track.clientWidth,
        selection:getComputedStyle(document.querySelector('#selected .ui-selection')).display,
        selectedDisabled:getComputedStyle(document.querySelector('#selected-disabled .ui-selection')).display,
        fontLoaded:document.fonts.status==='loaded',
        safeTop:document.querySelector('.audit-wrapper').getBoundingClientRect().top+parseFloat(getComputedStyle(document.querySelector('.audit-wrapper')).paddingTop)};
    }""")
    assert all(target['height'] >= 44 for target in data['targets']), (label, data)
    assert data['selected'] != data['unselected'], (label, data)
    assert data['disabledOpacity'] == '1', (label, data)
    assert data['disabledFill'] != data['primaryFill'], (label, data)
    assert data['selection'] != 'none' and data['selectedDisabled'] != 'none', (label, data)
    assert .54 < data['progress'] <= .61, (label, data)
    assert data['fontLoaded'] and data['safeTop'] >= 56, (label, data)
    assert page.locator('.audit-chips .ui-chip').evaluate_all("""chips=>chips.every(chip=>{
      const box=chip.getBoundingClientRect();
      return [...chip.childNodes].every(node=>{
        if(node.nodeType===Node.TEXT_NODE && !node.textContent.trim())return true;
        if(node.nodeType===Node.ELEMENT_NODE && getComputedStyle(node).display==='none')return true;
        const range=document.createRange();range.selectNode(node);const r=range.getBoundingClientRect();
        return r.left>=box.left+1 && r.right<=box.right-1;
      });
    })"""), label + ': selected chip content crosses its outline'
    expect(page.locator('#loading')).to_be_disabled()
    expect(page.locator('#loading')).to_have_attribute('aria-busy', 'true')
    assert page.locator('#loading .ui-spinner').evaluate("n=>getComputedStyle(n).animationName==='none'"), label
    return data


def text_contrast(page):
    """Check body copy against every stop in its actual panel gradients.

    Display text with decorative outlines needs visual review; this calculation
    intentionally covers unoutlined explanatory copy and semantic status text.
    """
    return page.evaluate("""() => {
      const rgb=s=>(s.match(/[\\d.]+/g)||[]).slice(0,3).map(Number);
      const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
      const contrast=(a,b)=>{a=lum(a);b=lum(b);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
      return [...document.querySelectorAll('#panel .audit-copy,#info .audit-copy,#success .audit-copy,#error .audit-copy')].map(node=>{
        const text=getComputedStyle(node), surface=getComputedStyle(node.parentElement);
        const colors=surface.backgroundImage.match(/rgba?\\([^)]*\\)/g)||[];
        if(surface.backgroundColor!=='rgba(0, 0, 0, 0)')colors.push(surface.backgroundColor);
        return {id:node.parentElement.id, color:text.color, stops:colors,
          minimum:colors.length?Math.min(...colors.map(color=>contrast(rgb(text.color),rgb(color)))):null};
      });
    }""")


def disabled_precedence(page):
    page.locator('#fixture').evaluate("""node=>node.insertAdjacentHTML('beforeend', `
      <section class="ui-nav" aria-label="Disabled navigation samples">
        <button class="ui-button ui-nav-item" aria-current="page" disabled id="nav-native"><span>Arena</span></button>
        <button class="ui-button ui-nav-item" aria-current="page" aria-disabled="true" id="nav-aria"><span>Arena</span></button>
      </section>
      <div class="ui-segmented ui-segmented-compact" role="group" aria-label="Disabled selection sample">
        <button class="ui-button" aria-pressed="true" aria-disabled="true" id="compact-disabled"><span class="ui-selection" aria-hidden="true">✓</span>RU</button>
        <button class="ui-button" aria-pressed="false">EN</button>
      </div>`)""")
    data = page.evaluate("""() => {
      const baseline=getComputedStyle(document.querySelector('#disabled'));
      return [...document.querySelectorAll('#nav-native,#nav-aria,#compact-disabled')].map(node=>{
        const css=getComputedStyle(node);
        return {id:node.id,background:css.backgroundImage,color:css.color,
          matchesDisabled:css.backgroundImage===baseline.backgroundImage && css.color===baseline.color,
          opacity:css.opacity};
      });
    }""")
    assert all(item['matchesDisabled'] and item['opacity'] == '1' and 'repeating-linear-gradient' in item['background'] for item in data), data
    expect(page.locator('#nav-native')).to_be_disabled()
    expect(page.locator('#compact-disabled')).to_have_attribute('aria-pressed', 'true')
    assert page.locator('#compact-disabled .ui-selection').evaluate("n=>getComputedStyle(n).display!=='none'")
    return data


def navigation_labels(page, label):
    data = page.locator('.ui-nav .ui-nav-item').evaluate_all("""nodes=>nodes.map(node=>{
      const label=node.querySelector('span:last-child'), box=node.getBoundingClientRect();
      const text=label.getBoundingClientRect(), lineHeight=parseFloat(getComputedStyle(label).lineHeight);
      return {text:label.innerText, height:text.height, lineHeight, width:box.width,
        contained:text.left>=box.left && text.right<=box.right};
    })""")
    assert len(data) == 4 and all(item['height'] <= item['lineHeight'] + 1 and item['contained'] and item['width'] >= 44 for item in data), (label, data)
    return data


def exercise(browser, url, artifacts, states_only=False):
    errors, report = [], []
    for width, height in ([] if states_only else SIZES):
        for language in ['ru', 'en']:
            for scheme in ['dark', 'light']:
                label = f'{width}x{height}-{language}-{scheme}'
                context = browser.new_context(viewport={'width': width, 'height': height},
                                              color_scheme=scheme, reduced_motion='reduce')
                context.add_init_script(BRIDGE.replace('SCHEME', json.dumps(scheme)))
                context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, body=''))
                context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
                context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url + '/__foundation_audit__?lang=' + language, wait_until='networkidle')
                page.wait_for_function('() => window.fixtureReady === true')
                page.evaluate('document.fonts.ready')
                assert page.evaluate("[...document.styleSheets].some(sheet=>sheet.href?.endsWith('/foundation.css') && sheet.cssRules.length>0)"), label
                expect(page.locator('.audit-icons .ui-icon')).to_have_count(12)
                data = component_geometry(page, label)
                contrasts = text_contrast(page)
                assert all(item['minimum'] is not None and item['minimum'] >= 4.5 for item in contrasts), (label, contrasts)
                page.screenshot(path=str(artifacts / f'{label}-components.png'), full_page=True)
                page.locator('#primary').focus()
                assert page.locator('#primary').evaluate("n => {const s=getComputedStyle(n); return parseFloat(s.outlineWidth)>=2 && s.outlineStyle!=='none';}"), label
                if label == '390x844-en-dark':
                    page.screenshot(path=str(artifacts / '390x844-en-keyboard-focus.png'))
                page.locator('#primary').hover()
                page.mouse.down()
                assert page.locator('#primary').evaluate("n => {const s=getComputedStyle(n);return s.transform==='none' && ['none','0px','0px 0px'].includes(s.translate);}"), label
                page.mouse.up()
                # The same real navigation must fit without redesigning any page.
                page.goto(url + '/#gear', wait_until='networkidle')
                expect(page.locator('#navigation')).to_be_visible()
                expect(page.locator('#navigation.ui-nav .ui-nav-item')).to_have_count(4)
                before = server_state(page)
                check_layout(page, label + '-navigation')
                nav = page.locator('#navigation').bounding_box()
                assert nav and nav['y'] + nav['height'] <= height - 30 + 1, (label, nav)
                assert page.locator('#navigation button').evaluate_all('nodes=>nodes.every(n=>n.getBoundingClientRect().height>=44)'), label
                navigation_labels(page, label)
                expect(page.locator('[data-tab=gear]')).to_have_attribute('aria-current', 'page')
                page.screenshot(path=str(artifacts / f'{label}-navigation.png'))
                page.locator('[data-tab=roost]').click()
                expect(page.locator('.roost-screen')).to_be_visible()
                page.locator('.roost-footnote').evaluate("n=>n.scrollIntoView({block:'center'})")
                assert page.locator('.roost-footnote').evaluate("n=>n.getBoundingClientRect().bottom<=document.querySelector('#navigation').getBoundingClientRect().top"), label
                after = server_state(page)
                assert before['player']['balance_minor'] == after['player']['balance_minor'], label
                assert before['player']['xp'] == after['player']['xp'], label
                report.append({'label': label, 'components': data, 'contrast': contrasts, 'navigation': nav})
                context.close()
                print('Passed:', label, flush=True)
    # Motion-enabled press lowers the surface; reduced-motion checks above avoid it.
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='no-preference')
    context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
    page = context.new_page()
    page.goto(url + '/__foundation_audit__?lang=en', wait_until='networkidle')
    states = disabled_precedence(page)
    assert page.locator('#loading .ui-spinner').evaluate("n=>getComputedStyle(n).animationName==='ui-spin'")
    page.locator('#primary').hover()
    before = page.locator('#primary').evaluate('n=>({transform:getComputedStyle(n).transform,translate:getComputedStyle(n).translate})')
    page.mouse.down()
    page.wait_for_timeout(180)
    after = page.locator('#primary').evaluate('n=>({transform:getComputedStyle(n).transform,translate:getComputedStyle(n).translate})')
    assert before != after, (before, after)
    page.screenshot(path=str(artifacts / '390x844-en-pressed.png'))
    page.mouse.up()
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('#loading .ui-spinner').evaluate("n=>getComputedStyle(n).animationName==='none'")
    page.locator('#compact-disabled').scroll_into_view_if_needed()
    page.screenshot(path=str(artifacts / '390x844-en-disabled-selected.png'))
    context.close()
    # Narrow navigation has four existing labels, including long Russian Gear.
    # Recheck both real consumers after changes to the shared column allocation.
    for language in ['ru', 'en']:
        for scheme in ['dark', 'light']:
            context = browser.new_context(viewport={'width': 320, 'height': 568}, color_scheme=scheme, reduced_motion='reduce')
            context.add_init_script(BRIDGE.replace('SCHEME', json.dumps(scheme)))
            context.route('https://telegram.org/js/telegram-web-app.js', lambda route: route.fulfill(status=200, body=''))
            context.add_init_script("localStorage.setItem('rooster.v1.language', %s)" % json.dumps(language))
            context.add_init_script("localStorage.setItem('rooster.v1.guideSeen.v1', '1')")
            page = context.new_page()
            for consumer, path in [('live', '/#gear'), ('preview', '/static/arena-preview/index.html?lang=' + language)]:
                page.goto(url + path, wait_until='networkidle')
                page.evaluate('document.fonts.ready')
                expect(page.locator('.ui-nav')).to_be_visible()
                label = f'320x568-{language}-{scheme}-{consumer}'
                report.append({'label': label, 'navigationLabels': navigation_labels(page, label)})
                page.screenshot(path=str(artifacts / f'{label}-nav-labels.png'))
            context.close()
    assert not errors, errors
    report.append({'disabledPrecedence': states, 'loadingMotion': 'normal animated; reduced motion static'})
    (artifacts / ('states-report.json' if states_only else 'report.json')).write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--states-only', action='store_true', help='Recheck disabled/selected/loading/pressed variants only')
    options = parser.parse_args()
    artifacts = ROOT / 'artifacts' / 'foundation'
    artifacts.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='roosters-foundation-') as temporary:
        app = create_app(Settings(app_env='test', allow_dev_auth=True, secret_key='foundation-audit' * 4,
                                 database_path=str(Path(temporary) / 'game.sqlite3')), clock=Clock())
        app.add_url_rule('/__foundation_audit__', 'foundation_audit', lambda: FIXTURE_HTML)
        app.add_url_rule('/__foundation_fixture__.mjs', 'foundation_fixture',
                         lambda: (FIXTURE_SCRIPT, {'Content-Type': 'text/javascript'}))
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietRequests)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
                browser = playwright.chromium.launch(headless=True, **({'executable_path': str(chrome)} if chrome.exists() else {}))
                try:
                    exercise(browser, f'http://127.0.0.1:{server.server_port}', artifacts, options.states_only)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
    print('Foundation checks passed:', 'selected/disabled/loading/pressed states' if options.states_only else '20 component/navigation cases plus selected/disabled/loading/pressed states', 'Screenshots:', artifacts)


if __name__ == '__main__':
    main()
