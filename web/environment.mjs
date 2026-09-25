/** Telegram/browser shell only. Never owns player state or rebuilds screen DOM. */
const palettes = {
  dark: { bg: '#10171f', panel: '#19232d', 'panel-light': '#202c37', text: '#f5f3eb', muted: '#91a1ac', line: '#2b3741', accent: '#ffad59', lime: '#d0eb89', red: '#fb816f' },
  light: { bg: '#f5f1e9', panel: '#fffaf2', 'panel-light': '#e8e2d8', text: '#292720', muted: '#625e55', line: '#d5cec2', accent: '#8a4500', lime: '#4c6925', red: '#b12f26' },
};
const hex = value => typeof value === 'string' && /^#[\da-f]{6}$/i.test(value) ? value : null;
const luminance = color => {
  const rgb = color.slice(1).match(/../g).map(n => parseInt(n, 16) / 255)
    .map(n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4);
  return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
};
export const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);

export function themeTokens(scheme, params = {}) {
  params = params || {};
  const base = palettes[scheme] || palettes.light;
  const tokens = { ...base };
  // Reject mixed/inverted surfaces: one readable foreground must work everywhere.
  for (const [token, key] of [['bg', 'bg_color'], ['panel', 'secondary_bg_color'], ['panel-light', 'section_bg_color']]) {
    const color = hex(params[key]);
    if (color && (scheme === 'dark' ? luminance(color) < .12 : luminance(color) > .65)) tokens[token] = color;
  }
  const surfaces = [tokens.bg, tokens.panel, tokens['panel-light']];
  const readable = color => color && surfaces.every(surface => contrast(color, surface) >= 4.5);
  for (const [token, key] of [['text', 'text_color'], ['muted', 'hint_color'], ['accent', null], ['lime', null], ['red', null]]) {
    tokens[token] = [hex(params[key]), base[token], scheme === 'dark' ? '#ffffff' : '#171717'].find(readable);
  }
  tokens.line = hex(params.section_separator_color) || base.line;
  // A separator identical to its surface conveys no hierarchy.
  if (surfaces.some(surface => contrast(tokens.line, surface) < 1.15)) tokens.line = tokens.muted;
  return tokens;
}

export function createAppEnvironment(win = window, doc = document) {
  const root = doc.documentElement;
  const bridge = win.Telegram?.WebApp;
  // The official SDK also creates WebApp in a normal browser (platform unknown).
  const available = Boolean(bridge && (bridge.initData || (bridge.platform && bridge.platform !== 'unknown')
    || (!bridge.platform && (bridge.colorScheme || bridge.viewportStableHeight))));
  const telegram = available ? bridge : null;
  const media = win.matchMedia('(prefers-color-scheme: dark)');
  const set = (name, value) => root.style.setProperty(name, value);
  const supports = version => {
    try { return !telegram?.isVersionAtLeast || telegram.isVersionAtLeast(version); } catch { return false; }
  };
  const call = (method, version, ...args) => {
    try { if (telegram && supports(version) && typeof telegram[method] === 'function') telegram[method](...args); } catch { /* Older/partial native bridges must not block boot. */ }
  };
  const on = (event, callback, version = '6.0') => call('onEvent', version, event, callback);
  const state = { available, theme: 'light', stableHeight: null, active: telegram?.isActive !== false };
  let compactHeight = 0;
  let started = false;
  let resume = () => {};
  function syncTheme() {
    state.theme = telegram?.colorScheme === 'dark' || (telegram?.colorScheme !== 'light' && media.matches) ? 'dark' : 'light';
    root.dataset.theme = state.theme;
    root.style.colorScheme = state.theme;
    const tokens = themeTokens(state.theme, telegram?.themeParams);
    // The approved game palette applies in both native themes. Resolve it from
    // the shared CSS source; retain the readable native fallback if CSS is absent.
    // Event ownership, SDK version gates and viewport accounting stay unchanged.
    const shared = win.getComputedStyle(root);
    for (const [key, role] of Object.entries({ bg: 'bg', panel: 'panel', 'panel-light': 'panel-raised',
      text: 'text', muted: 'muted-on-blue', line: 'ink', accent: 'gold', amber: 'gold', lime: 'success', red: 'error' })) {
      const value = hex(shared.getPropertyValue(`--ui-${role}`).trim());
      if (value) tokens[key] = value;
    }
    for (const [key, value] of Object.entries(tokens)) set(`--${key}`, value);
    doc.querySelector('meta[name="theme-color"]')?.setAttribute('content', tokens.bg);
    call('setHeaderColor', '6.1', supports('6.9') ? tokens.bg : 'bg_color');
    call('setBackgroundColor', '6.1', tokens.bg);
    call('setBottomBarColor', '7.10', tokens.bg);
  }
  function syncCompactLayout() {
    if (!compactHeight) return;
    // Existing short-screen layouts use the space left after native chrome.
    const css = win.getComputedStyle(root);
    let insets = 0;
    for (const edge of ['top', 'bottom']) {
      for (const [source, prefix] of [[telegram?.safeAreaInset, 'safe-area'], [telegram?.contentSafeAreaInset, 'content-safe-area']]) {
        const value = source?.[edge];
        insets += Number.isFinite(value) && value >= 0 ? value
          : telegram ? Math.max(0, parseFloat(css.getPropertyValue(`--tg-${prefix}-inset-${edge}`)) || 0) : 0;
      }
    }
    root.dataset.compactBattle = String(compactHeight - insets <= 650);
    // Framed shared controls need the dense setup through medium-height phones.
    root.dataset.compactArena = String(compactHeight - insets <= 700);
  }
  function syncInsets() {
    for (const edge of ['top', 'right', 'bottom', 'left']) {
      for (const [prefix, source, css, fallback] of [
        ['safe', telegram?.safeAreaInset, 'safe-area', `env(safe-area-inset-${edge}, 0px)`],
        ['content-safe', telegram?.contentSafeAreaInset, 'content-safe-area', '0px'],
      ]) {
        const value = source?.[edge];
        set(`--app-${prefix}-${edge}`, Number.isFinite(value) && value >= 0 ? `${value}px`
          : telegram ? `var(--tg-${css}-inset-${edge}, ${fallback})` : fallback);
      }
    }
    syncCompactLayout();
  }
  function syncViewport(event) {
    if (event?.isStateStable === false) return;
    const height = telegram?.viewportStableHeight;
    state.stableHeight = Number.isFinite(height) && height > 0 ? height : null;
    const cssHeight = parseFloat(win.getComputedStyle(root).getPropertyValue('--tg-viewport-stable-height'));
    compactHeight = state.stableHeight || (telegram && cssHeight > 0 ? cssHeight : win.innerHeight);
    syncCompactLayout();
    // Never clamp a Telegram stable height to a transient browser dvh/innerHeight.
    set('--app-height', state.stableHeight ? `${height}px`
      : telegram ? 'var(--tg-viewport-stable-height, 100svh)' : '100dvh');
  }
  function sync() { syncTheme(); syncInsets(); syncViewport(); }
  function activate() {
    state.active = true;
    sync();
    resume();
  }
  root.dataset.telegram = String(available);
  sync();
  on('themeChanged', syncTheme);
  on('viewportChanged', syncViewport);
  on('safeAreaChanged', syncInsets, '8.0');
  on('contentSafeAreaChanged', syncInsets, '8.0');
  on('activated', activate, '8.0');
  on('deactivated', () => { state.active = false; }, '8.0');
  const themeChange = () => { if (!telegram) syncTheme(); };
  if (media.addEventListener) media.addEventListener('change', themeChange);
  else media.addListener?.(themeChange);
  doc.addEventListener('visibilitychange', () => { if (!doc.hidden) activate(); });
  win.addEventListener('resize', () => { if (!telegram) syncViewport(); });
  win.addEventListener('pageshow', event => { if (event.persisted) activate(); });
  return {
    telegram, state,
    onResume(callback) { resume = callback; },
    start() {
      if (started) return;
      started = true;
      // The localized loading shell is painted without waiting for authentication.
      call('ready', '6.0');
      call('expand', '6.0');
    },
  };
}
