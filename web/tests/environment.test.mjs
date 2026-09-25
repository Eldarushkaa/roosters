import assert from 'node:assert/strict';
import test from 'node:test';
import { createAppEnvironment, themeTokens, contrast } from '../environment.mjs';

function harness(bridge, dark = false) {
  const css = new Map();
  const events = new Map();
  const native = [];
  const documentEvents = new Map();
  const windowEvents = new Map();
  const root = { dataset: {}, style: { setProperty: (key, value) => css.set(key, value) } };
  const media = { matches: dark, addEventListener: (_, fn) => { media.change = fn; } };
  if (bridge) {
    bridge.onEvent = (name, fn) => events.set(name, fn);
    for (const method of ['ready', 'expand', 'setHeaderColor', 'setBackgroundColor', 'setBottomBarColor']) {
      bridge[method] = (...args) => native.push([method, ...args]);
    }
  }
  const doc = { documentElement: root, querySelector: () => ({ setAttribute() {} }),
    addEventListener: (name, fn) => documentEvents.set(name, fn) };
  const win = { Telegram: bridge && { WebApp: bridge }, innerHeight: 844, matchMedia: () => media,
    getComputedStyle: () => ({ getPropertyValue: key => css.get(key) || '' }),
    addEventListener: (name, fn) => windowEvents.set(name, fn) };
  const env = createAppEnvironment(win, doc);
  return { env, css, events, native, root, media, win, doc, documentEvents, windowEvents };
}

test('browser with absent or unlaunched SDK follows live system theme', () => {
  for (const bridge of [undefined, { platform: 'unknown', colorScheme: 'light', initData: '' }]) {
    const h = harness(bridge, true);
    assert.equal(h.env.state.available, false);
    assert.equal(h.root.dataset.theme, 'dark');
    h.media.matches = false; h.media.change();
    assert.equal(h.root.dataset.theme, 'light');
    assert.equal(h.css.get('--app-safe-bottom'), 'env(safe-area-inset-bottom, 0px)');
    assert.equal(h.css.get('--app-height'), '100dvh');
    assert.equal(h.native.length, 0);
  }
});

test('theme palette keeps readable semantic text for incomplete and conflicting Telegram colors', () => {
  for (const scheme of ['light', 'dark']) {
    for (const params of [null, {}, { bg_color: '#888888', text_color: '#888888', hint_color: '#888888' },
      { bg_color: '#000000', secondary_bg_color: '#ffffff', text_color: '#000000', hint_color: '#ffffff', button_color: '#ff00ff' },
      { bg_color: '#eeeeee', secondary_bg_color: '#eeeeee', section_bg_color: '#eeeeee', text_color: 'invalid', hint_color: '#eeeeee' }]) {
      const palette = themeTokens(scheme, params);
      for (const token of ['text', 'muted', 'accent', 'lime', 'red']) {
        for (const surface of ['bg', 'panel', 'panel-light']) assert.ok(contrast(palette[token], palette[surface]) >= 4.5);
      }
      assert.notEqual(palette.accent, '#ff00ff');
    }
  }
});

test('unstable viewport and browser resize do not move Telegram controls or compact layout', () => {
  const bridge = { platform: 'ios', colorScheme: 'dark', viewportStableHeight: 844 };
  const h = harness(bridge);
  bridge.viewportHeight = 500; bridge.viewportStableHeight = 560;
  h.events.get('viewportChanged')({ isStateStable: false });
  h.win.innerHeight = 500; h.windowEvents.get('resize')();
  assert.equal(h.css.get('--app-height'), '844px');
  assert.equal(h.root.dataset.compactBattle, 'false');
  h.events.get('viewportChanged')({ isStateStable: true });
  assert.equal(h.css.get('--app-height'), '560px');
  assert.equal(h.root.dataset.compactBattle, 'true');
});

test('safe areas replace browser insets including explicit zero; content area remains separate', () => {
  const bridge = { platform: 'android', safeAreaInset: { top: 24, bottom: 0 }, contentSafeAreaInset: { top: 20, bottom: 10 } };
  const h = harness(bridge);
  assert.equal(h.css.get('--app-safe-bottom'), '0px');
  assert.equal(h.css.get('--app-content-safe-bottom'), '10px');
  assert.equal(h.css.get('--app-safe-top'), '24px');
  bridge.safeAreaInset.bottom = 34; h.events.get('safeAreaChanged')();
  assert.equal(h.css.get('--app-safe-bottom'), '34px');
  bridge.contentSafeAreaInset.bottom = 0; h.events.get('contentSafeAreaChanged')();
  assert.equal(h.css.get('--app-content-safe-bottom'), '0px');
});

test('version gates header keywords, bottom bar and activation; ready/expand happen once', () => {
  for (const version of ['6.0', '6.1', '6.9', '7.10', '8.0']) {
    const atLeast = value => {
      const [major, minor] = version.split('.').map(Number), [a, b] = value.split('.').map(Number);
      return major > a || major === a && minor >= b;
    };
    const h = harness({ platform: 'ios', colorScheme: 'dark', isVersionAtLeast: atLeast });
    h.env.start(); h.env.start();
    assert.equal(h.native.filter(([name]) => name === 'ready').length, 1);
    assert.equal(h.native.filter(([name]) => name === 'expand').length, 1);
    assert.equal(h.native.some(([name]) => name === 'setBottomBarColor'), atLeast('7.10'));
    assert.equal(h.events.has('activated'), atLeast('8.0'));
    if (atLeast('6.1')) assert.equal(h.native.find(([name]) => name === 'setHeaderColor')[1], atLeast('6.9') ? '#10171f' : 'bg_color');
  }
});

test('activation and browser resume resync shell and invoke existing state recovery', () => {
  const bridge = { platform: 'ios', colorScheme: 'dark', viewportStableHeight: 844 };
  const h = harness(bridge);
  let resumed = 0; h.env.onResume(() => resumed++);
  h.events.get('deactivated')(); assert.equal(h.env.state.active, false);
  bridge.colorScheme = 'light'; bridge.viewportStableHeight = 640;
  h.events.get('activated')();
  assert.equal(h.env.state.active, true);
  assert.equal(h.root.dataset.theme, 'light');
  assert.equal(h.css.get('--app-height'), '640px');
  assert.equal(resumed, 1);
  h.doc.hidden = true; h.documentEvents.get('visibilitychange')(); assert.equal(resumed, 1);
  h.doc.hidden = false; h.documentEvents.get('visibilitychange')(); assert.equal(resumed, 2);
});

test('unsupported native chrome failures cannot prevent ready or app initialization', () => {
  const h = harness({ platform: 'ios' });
  h.env.telegram.setHeaderColor = () => { throw Error('not supported'); };
  assert.doesNotThrow(() => h.events.get('themeChanged')());
  h.env.start();
  assert.ok(h.native.some(([name]) => name === 'ready'));
});


test('native insets activate existing short layouts without shrinking stable viewport twice', () => {
  const bridge = { platform: 'ios', viewportStableHeight: 640, safeAreaInset: { top: 24, bottom: 20 }, contentSafeAreaInset: { top: 16, bottom: 10 } };
  const h = harness(bridge);
  assert.equal(h.root.dataset.compactArena, 'true');
  assert.equal(h.css.get('--app-height'), '640px');
  bridge.safeAreaInset = {top: 0, bottom: 0};
  bridge.contentSafeAreaInset = {top: 0, bottom: 0};
  h.events.get('contentSafeAreaChanged')();
  assert.equal(h.root.dataset.compactArena, 'false');
});
