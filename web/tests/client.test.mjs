/** Transport regressions with a minimal DOM; real layout is browser-tested. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import test from 'node:test';
import vm from 'node:vm';
import { createI18n, botNameKeys, LANGUAGE_STORAGE_KEY } from '../i18n.mjs';
import { icon, iconText } from '../icons.mjs';
import { createFeedback } from '../feedback.mjs';
import { renderRooster, roosterBattleFrame } from '../rooster-art.mjs';

const source = (await readFile(new URL('../app.js', import.meta.url), 'utf8'))
  .replace(/^import .* from '\.\/i18n\.mjs';\n/m, '')
  .replace(/^import .* from '\.\/environment\.mjs';\n/m, '')
  .replace(/^import .* from '\.\/(icons|feedback|guide|rooster-art)\.mjs';\n/gm, '')
  .replace(/\nboot\(\);\s*$/, '\n');
const playerState = (id = 'dev:tester') => ({
  server_time: Date.now() / 1000,
  rules_version: 'v6',
  player: { id, name: 'Игрок', is_dev: id.startsWith('dev:'), balance_minor: 42000, power: 100, breed_id: 'yard', owned_breeds: ['yard'], level: 1, xp_in_level: 0, xp_to_next: 100, wins: 0, losses: 0, battles: 0, pvp_wins: 0, gear: {} },
  economy: { first_free_battle_available: true, passive_available_minor: 0, daily_reward_minor: 17000, daily_available: true, next_daily_at: 0, upgrade_costs_minor: {}, stake_limits: { min_minor: 1000, max_minor: 42000, step_minor: 1 }, bot_quotes: [1000, 2500, 5000, 10000].map((stake_minor) => ({ stake_minor, min_payout_minor: stake_minor * 1.5, max_payout_minor: stake_minor * 2.5 })), online_quotes: [{ stake_minor: 1000, win_payout_minor: 1900 }, { stake_minor: 2500, win_payout_minor: 4750 }, { stake_minor: 5000, win_payout_minor: 9500 }, { stake_minor: 10000, win_payout_minor: 19000 }] },
  catalog: { breeds: [{ id: 'yard', name: 'Дворовый', price_minor: 0, power_multiplier: 1, power_multiplier_percent: 100, color: '#f3ad55' }], slots: [], referral: { signup_reward_minor: 30000, battle_reward_minor: 30000, battles_required: 3 }, battle: { duration: 10, roulette_duration: 2, tap_cap: 90, stakes_minor: [1000, 2500, 5000, 10000], bot_power_min_ratio: 0.7, bot_power_max_ratio: 1.4, bot_rtp_min: 0.9, bot_rtp_max: 1.10, pvp_win_multiplier: 1.9, pvp_rtp: 1.2, free_reward_minor: 1500 } },
  presence: { online: 0, searching: 0, development_online: 1 }, history: [], reward_events: [], queue: null, battle: null,
});
const reply = (status, payload) => ({ status, ok: status >= 200 && status < 300, json: async () => payload });
const customQuote = (stake_minor, power = 100) => ({
  rules_version: 'v6', power, stake_minor,
  bot: { min_payout_minor: 17001, max_payout_minor: 24002 },
  online: { win_payout_minor: 23003 },
});
const rangeTag = (html) => html.match(/<input\b[^>]*id="arena-stake-range"[^>]*>/)?.[0] ?? '';
const actionTag = (html, action) => html.match(new RegExp(`<button\\b[^>]*data-action="${action}"[^>]*>`))?.[0] ?? '';

function harness({ fetch, entries = {}, localEntries = {}, initData = '' } = {}) {
  const session = new Map(Object.entries(entries));
  const local = new Map(Object.entries(localEntries));
  const store = (data) => ({ getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: (key) => data.delete(key) });
  const elements = new Map();
  const element = () => ({ hidden: false, dataset: {}, style: {}, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, replaceChildren() {}, querySelector: () => null, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {}, focus() { sandbox.document.activeElement = this; } });
  const app = { initData, ready() {}, expand() {} };
  const intervals = [];
  const timeouts = new Map();
  let nextTimeout = 0;
  const listeners = new Map();
  const guideCalls = [];
  let guideMock;
  const sandbox = {
    createAppEnvironment: () => ({ telegram: app, start() { app.ready(); app.expand(); }, onResume(callback) { listeners.set('visibilitychange', callback); } }),
    icon, iconText, renderRooster, roosterBattleFrame, createFeedback: options => createFeedback({...options, win: {}, doc: {querySelector: () => null}}),
    createGuide(options) {
      let available = false;
      let opened = false;
      let seen = local.get('rooster.v1.guideSeen.v1') === '1';
      guideMock = {
        sync(value) {
          guideCalls.push({ action: 'sync', ...value });
          available = value.available;
          if (!available) opened = false;
          else if (value.autoAllowed && !seen) opened = true;
        },
        open() { guideCalls.push({ action: 'open' }); if (available) opened = true; },
        close() {
          guideCalls.push({ action: 'close' });
          if (opened) { opened = false; seen = true; local.set('rooster.v1.guideSeen.v1', '1'); }
        },
        isOpen: () => opened,
        content: () => options.getContent(),
      };
      return guideMock;
    },
    crypto: webcrypto, Intl, URLSearchParams, AbortController, Date, console, createI18n, botNameKeys,
    fetch: fetch || (async () => { throw new Error('Unexpected fetch'); }),
    sessionStorage: store(session), localStorage: store(local),
    location: { hash: '' }, history: { replaceState() {} },
    navigator: {},
    window: { Telegram: { WebApp: app }, addEventListener() {}, scrollTo() {} },
    document: {
      hidden: false, activeElement: null, documentElement: {}, addEventListener(name, handler) { listeners.set(name, handler); }, querySelectorAll: () => [],
      querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
    },
    setTimeout(fn, delay) { const id = ++nextTimeout; timeouts.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(fn, delay) { intervals.push({ fn, delay }); return intervals.length; },
  };
  vm.createContext(sandbox);
  new vm.Script(source + `
    const actualShowNotice = showNotice;
    const actualClearNotice = clearNotice;
    render = () => {};
    showNotice = (message, options) => { globalThis.lastNotice = {message, options}; };
    clearNotice = () => {};
    globalThis.client = {
      mutate, sendPending, request, currentAuthContext, boot, handleAction, refreshState,
      renderArena, renderQueue, renderBattle, renderRoost, renderResult, renderGear,
      renderHistory, renderLeaderboard, updateTimers, money, signedMoney, formatDate,
      acceptState, renderResultToast, closeResultToast,
      currentStake, currentStakeQuotes, selectStake, ensureStakeQuote, loadStakeQuote,
      syncGuide,
      changeLanguage, messageText, fighterName, localizedError, errorMessage, actualShowNotice, actualClearNotice,
      getLanguage() { return i18n.getLanguage(); },
      getBattleBuffer() { return { count: battleBuffer, battleId: bufferedBattleId }; },
      setLeaderboard(value) { leaderboard = value; },
      setState(value) { state = value; },
      setServerNow(value) { serverOffset = value * 1000 - Date.now(); },
      setConfig(value) { config = value; },
      getPending() { return pending; },
      getState() { return state; },
      disableRefresh() { refreshState = async () => { globalThis.refreshed = true; }; },
    };
  `).runInContext(sandbox);
  return { ...sandbox, session, local, app, context: sandbox, intervals, timeouts, listeners, guideMock, guideCalls };
}

test('lost purchase response retries the identical UUID and body only once', async () => {
  const sent = [];
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, key: options.headers['Idempotency-Key'], body: options.body });
    if (sent.length === 1) throw new TypeError('Response lost after commit');
    return reply(200, { state: playerState(), result: {} });
  } });
  env.client.setState(playerState());
  assert.equal(await env.client.mutate('/gear/upgrade', { slot: 'sword' }), false);
  const saved = JSON.parse(env.session.get('rooster.v1.pending'));
  assert.match(saved.key, /^[0-9a-f-]{36}$/);
  assert.equal(await env.client.mutate('/gear/upgrade', { slot: 'sword' }), false);
  assert.equal(sent.length, 1, 'An unresolved purchase blocks a new command');
  assert.equal(await env.client.sendPending(), true);
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(env.client.getPending(), null);
  assert.equal(env.session.has('rooster.v1.pending'), false);
});

test('server 503 and malformed success preserve the pending command', async () => {
  for (const response of [reply(503, { error: { message: 'Busy' } }), reply(200, { result: {} })]) {
    const env = harness({ fetch: async () => response });
    env.client.setState(playerState());
    assert.equal(await env.client.mutate('/claim/daily', {}), false);
    assert.equal(env.client.getPending().path, '/claim/daily');
    assert.ok(env.session.has('rooster.v1.pending'));
  }
});

test('definite queue cancellation conflict clears command and fetches raced match', async () => {
  const env = harness({ fetch: async () => reply(409, { error: { message: 'Match already started' } }) });
  env.client.setState(playerState());
  env.client.disableRefresh();
  assert.equal(await env.client.mutate('/queue/leave', {}), false);
  assert.equal(env.client.getPending(), null);
  assert.equal(env.context.refreshed, true);
});

test('failed session renewal retains the key of a possibly committed mutation', async () => {
  const requests = [];
  const env = harness({ initData: 'expired-launch-data', fetch: async (url) => {
    requests.push(url);
    return reply(401, { error: { message: 'Expired authorization' } });
  } });
  env.client.setState(playerState('tg:42'));
  assert.equal(await env.client.mutate('/battle/start', { mode: 'bot' }), false);
  assert.equal(env.client.getPending().path, '/battle/start');
  assert.deepEqual(requests, ['/api/v1/battle/start', '/api/v1/auth/telegram']);
});

test('same Telegram account reuses a valid bearer despite an old launch payload', async () => {
  const requests = [];
  const launch = new URLSearchParams({ user: JSON.stringify({ id: 42 }), auth_date: '1' }).toString();
  const env = harness({ initData: launch, entries: { 'rooster.v1.token': 'signed-existing', 'rooster.v1.authContext': 'tg:42' }, fetch: async (url, options) => {
    requests.push(url);
    if (url.endsWith('/config')) return reply(200, { dev_auth: false });
    assert.equal(options.headers.Authorization, 'Bearer signed-existing');
    if (url.endsWith('/state')) return reply(200, playerState('tg:42'));
    if (url.endsWith('/presence')) return reply(200, { state: playerState('tg:42'), result: {} });
    throw new Error(`Unexpected fresh auth: ${url}`);
  } });
  await env.client.boot();
  assert.deepEqual(requests, ['/api/v1/config', '/api/v1/state', '/api/v1/presence']);
  assert.equal(env.client.getState().player.id, 'tg:42');
});

test('a cached development account cannot replace a Telegram launch identity', async () => {
  const requests = [];
  const launch = new URLSearchParams({ user: JSON.stringify({ id: 42 }), auth_date: '1' }).toString();
  const env = harness({ initData: launch, entries: { 'rooster.v1.token': 'old-dev-session', 'rooster.v1.authContext': 'dev:tester' }, fetch: async (url, options) => {
    requests.push(url);
    if (url.endsWith('/config')) return reply(200, { dev_auth: true });
    if (url.endsWith('/auth/telegram')) {
      assert.equal(JSON.parse(options.body).init_data, launch);
      assert.equal(options.headers.Authorization, undefined);
      return reply(200, { token: 'new-telegram-session', state: playerState('tg:42') });
    }
    if (url.endsWith('/presence')) return reply(200, { state: playerState('tg:42'), result: {} });
    throw new Error(`Unexpected dev request: ${url}`);
  } });
  await env.client.boot();
  assert.deepEqual(requests, ['/api/v1/config', '/api/v1/auth/telegram', '/api/v1/presence']);
  assert.equal(env.session.get('rooster.v1.authContext'), 'tg:42');
});

test('automatic guide opening waits for boot recovery of the original pending command', async () => {
  const state = playerState('tg:42');
  const command = { path: '/claim/daily', body: {}, key: 'recover-before-guide', owner: 'tg:42', attempts: 1 };
  const launch = new URLSearchParams({ user: JSON.stringify({ id: 42 }), auth_date: '1' }).toString();
  const sent = [];
  let finishRecovery;
  let recoveryStarted;
  const recovering = new Promise(resolve => { recoveryStarted = resolve; });
  const env = harness({ initData: launch, entries: {
    'rooster.v1.token': 'active-session', 'rooster.v1.authContext': 'tg:42', 'rooster.v1.pending': JSON.stringify(command),
  }, fetch: (url, options) => {
    sent.push({ url, key: options.headers['Idempotency-Key'], body: options.body });
    if (url.endsWith('/config')) return Promise.resolve(reply(200, { dev_auth: false }));
    if (url.endsWith('/state')) return Promise.resolve(reply(200, state));
    if (url.endsWith('/claim/daily')) return new Promise(resolve => { finishRecovery = resolve; recoveryStarted(); });
    throw new Error(`Unexpected guide request: ${url}`);
  } });
  const boot = env.client.boot();
  await recovering;
  // Render is stubbed in this transport harness; synchronize at the same point
  // where the live render runs while recovery remains unresolved.
  env.client.syncGuide();
  assert.equal(env.guideMock.isOpen(), false);
  assert.equal(env.guideCalls.at(-1).autoAllowed, false);
  assert.equal(env.client.getPending().key, command.key);
  finishRecovery(reply(200, { state, result: {} }));
  await boot;
  assert.equal(env.client.getPending(), null);
  assert.equal(env.guideCalls.at(-1).available, true);
  assert.equal(env.guideCalls.at(-1).autoAllowed, true);
  assert.equal(env.guideMock.isOpen(), true);
  assert.deepEqual(sent.map(item => item.url), ['/api/v1/config', '/api/v1/state', '/api/v1/claim/daily']);
  assert.equal(sent[2].key, command.key);
  assert.equal(sent[2].body, '{}');
});

test('manual guide actions preserve a pending command, selected stake and mode without transport', async () => {
  const command = { path: '/gear/upgrade', body: { slot: 'sword' }, key: 'keep-command-during-guide', owner: 'dev:tester', attempts: 1 };
  const sent = [];
  const env = harness({ entries: {
    'rooster.v1.pending': JSON.stringify(command), 'rooster.v1.stakeMinor': '2500', 'rooster.v1.arenaMode': 'online',
  }, fetch: async (url) => { sent.push(url); throw new Error('Guide must be read-only'); } });
  const state = playerState();
  env.client.setState(state);
  env.client.syncGuide();
  assert.equal(env.guideMock.isOpen(), false);
  const beforeState = JSON.stringify(state);
  const beforePending = JSON.stringify(env.client.getPending());
  const savedPending = env.session.get('rooster.v1.pending');
  await env.client.handleAction('open-guide');
  assert.equal(env.guideMock.isOpen(), true);
  await env.client.handleAction('close-guide');
  assert.equal(env.guideMock.isOpen(), false);
  assert.equal(JSON.stringify(state), beforeState);
  assert.equal(JSON.stringify(env.client.getPending()), beforePending);
  assert.equal(env.session.get('rooster.v1.pending'), savedPending);
  assert.equal(env.client.currentStake(), 2500);
  assert.equal(env.session.get('rooster.v1.arenaMode'), 'online');
  assert.deepEqual(sent, []);
});

test('an open guide blocks game actions while its content follows the current catalog and locale', async () => {
  const sent = [];
  const env = harness({ entries: { 'rooster.v1.stakeMinor': '2500' }, localEntries: { 'rooster.v1.guideSeen.v1': '1' }, fetch: async (url) => {
    sent.push(url);
    throw new Error('An informational guide cannot start a battle');
  } });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  state.catalog.battle.tap_cap = 77;
  state.catalog.battle.duration = 12;
  env.client.setState(state);
  env.client.syncGuide();
  await env.client.handleAction('open-guide');
  const before = JSON.stringify(state);
  assert.match(env.guideMock.content(), /77 нажатий за 12 секунд/);
  assert.match(env.guideMock.content(), /при поражении теряешь ставку/);
  await env.client.handleAction('start-bot', { dataset: {} });
  await env.client.handleAction('stake', { dataset: { stakeMinor: '10000' } });
  await env.client.handleAction('arena-mode', { dataset: { mode: 'online' } });
  env.client.changeLanguage('en');
  assert.match(env.guideMock.content(), /77 taps in 12 seconds/);
  assert.match(env.guideMock.content(), /Losing a paid battle costs your stake/);
  assert.equal(env.guideMock.isOpen(), true);
  assert.equal(env.client.currentStake(), 2500);
  assert.equal(env.session.get('rooster.v1.arenaMode'), undefined);
  assert.equal(JSON.stringify(state), before);
  assert.equal(env.client.getPending(), null);
  assert.deepEqual(sent, []);
});

test('guide availability follows queue, active battle and result completion without consuming deferred introduction', async () => {
  const env = harness();
  const state = playerState();
  state.queue = { joined_at: 0, expires_at: 120, stake_minor: 1000 };
  env.client.setState(state);
  env.client.syncGuide();
  assert.equal(env.guideCalls.at(-1).available, true);
  assert.equal(env.guideCalls.at(-1).autoAllowed, false);
  assert.equal(env.guideMock.isOpen(), false);
  await env.client.handleAction('open-guide');
  assert.equal(env.guideMock.isOpen(), true, 'Manual guidance remains available during matchmaking');

  state.queue = null;
  state.battle = { id: 'guide-battle', status: 'active' };
  env.client.syncGuide();
  assert.equal(env.guideCalls.at(-1).available, false);
  assert.equal(env.guideMock.isOpen(), false);
  assert.equal(env.local.has('rooster.v1.guideSeen.v1'), false);
  await env.client.handleAction('open-guide');
  assert.equal(env.guideMock.isOpen(), false);

  state.battle = { id: 'guide-battle', status: 'finished', result: { won: true, payout_minor: 1800, net_minor: 800, xp: 25 } };
  env.client.acceptState(state);
  env.client.syncGuide();
  assert.equal(env.guideCalls.at(-1).available, true);
  assert.equal(env.guideCalls.at(-1).autoAllowed, false);
  assert.equal(env.guideMock.isOpen(), false);
  await env.client.handleAction('close-result');
  assert.equal(env.guideCalls.at(-1).autoAllowed, true);
  assert.equal(env.guideMock.isOpen(), true);
});

test('free, paid and matchmaking actions send immutable minor-unit stakes', async () => {
  const sent = [];
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, body: JSON.parse(options.body) });
    return reply(200, { state: playerState(), result: {} });
  } });
  env.client.setState(playerState());
  for (const action of ['start-free', 'start-bot', 'queue-join']) {
    await env.client.handleAction(action, { dataset: {} });
  }
  assert.deepEqual(sent, [
    { url: '/api/v1/battle/start', body: { mode: 'bot', stake_minor: 0, expected_power: 100 } },
    { url: '/api/v1/battle/start', body: { mode: 'bot', stake_minor: 1000, expected_power: 100 } },
    { url: '/api/v1/queue/join', body: { stake_minor: 1000 } },
  ]);
});

test('Training is the current bot label while the fight action and server mode stay unchanged', () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  assert.match(env.client.renderArena(), /Тренировка/);
  assert.match(env.client.renderArena(), /data-action="start-bot"/);
  assert.match(env.client.renderArena(), /data-mode="bot"/);
  env.client.changeLanguage('en');
  assert.match(env.client.renderArena(), /Training/);
  assert.doesNotMatch(env.client.renderArena(), /data-action="start-practice"/);
});

test('arena discloses wagers through mode help and renders no old training, energy or second currency', async () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  env.client.setConfig({ bot_username: '' });
  const closed = env.client.renderArena();
  assert.doesNotMatch(closed, /15–25|При поражении выплата 0/);
  await env.client.handleAction('mode-help', { dataset: { mode: 'bot' } });
  const arena = env.client.renderArena();
  assert.match(arena, /Бой длится 10 секунд/);
  assert.match(arena, /data-action="start-bot"/);
  assert.doesNotMatch(arena, /data-action="queue-join"/);
  assert.match(arena, /data-action="arena-mode" data-mode="online"/);
  assert.match(arena, /15–25/);
  assert.match(arena, /При поражении выплата 0/);
  assert.match(arena, /110% с 90 тапами/);
  state.queue = { joined_at: 0, expires_at: 120, stake_minor: 1000 };
  const queue = env.client.renderQueue();
  const battle = env.client.renderBattle({
    id: 'battle-v3', mode: 'bot', starts_at: 0, ends_at: 10, tap_cap: 80, wager: { stake_minor: 1000, win_payout_minor: 2000 },
    you: { name: 'Игрок', power: 100, taps: 0 },
    opponent: { name: 'Бот', power: 100, taps: 0, is_bot: true },
  });
  assert.match(battle, /0 \/ 80/);
  for (const html of [arena, queue, battle, env.client.renderRoost()]) {
    assert.doesNotMatch(html, /stance|[Тт]актик|Натиск|Защита|Обман|data-action="train"|start-practice|[Ээ]нерг|медал/);
  }
});

test('Arena mode selection preserves the stake and changes only the chosen fight action', async () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  await env.client.handleAction('stake', { dataset: { stakeMinor: '2500' } });
  await env.client.handleAction('arena-mode', { dataset: { mode: 'online' } });
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  const html = env.client.renderArena();
  assert.match(html, /data-action="queue-join"/);
  assert.doesNotMatch(html, /data-action="start-bot"/);
  assert.match(html, /47,5 <svg[^>]+data-icon="coin"/);
  assert.match(html, /−25 <svg[^>]+data-icon="coin"/);
  assert.equal(env.session.get('rooster.v1.arenaMode'), 'online');
  const restored = harness({ entries: Object.fromEntries(env.session) });
  restored.client.setState(state);
  assert.match(restored.client.renderArena(), /Найти бой · 25 <svg[^>]+data-icon="coin"/);
});

test('custom stake input updates exact minor units locally and debounces authoritative quotes', async () => {
  const sent = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: async (url, options) => {
    sent.push({ url, method: options.method, body: options.body });
    return reply(200, customQuote(12345));
  } });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  await env.client.handleAction('stake-range', { value: '12344' });
  env.client.ensureStakeQuote();
  await env.client.handleAction('stake-range', { value: '12345' });
  env.client.ensureStakeQuote();
  assert.equal(env.client.currentStake(), 12345);
  assert.equal(env.session.get('rooster.v1.stakeMinor'), '12345');
  let html = env.client.renderArena();
  const range = rangeTag(html);
  assert.match(range, /type="range"/);
  assert.match(range, /min="1000"/);
  assert.match(range, /max="42000"/);
  assert.match(range, /step="1"/);
  assert.match(range, /value="12345"/);
  assert.match(html, /id="arena-stake-value"[^>]*>123,45/);
  assert.match(actionTag(html, 'start-bot'), / disabled/);
  assert.equal(env.client.getPending(), null);
  assert.deepEqual(sent, []);
  assert.equal([...env.timeouts.values()].filter(item => item.delay === 150).length, 1);

  await env.client.loadStakeQuote();
  assert.deepEqual(sent, [{ url: '/api/v1/battle/quote?stake_minor=12345', method: 'GET', body: undefined }]);
  await env.client.handleAction('mode-help', { dataset: { mode: 'bot' } });
  html = env.client.renderArena();
  assert.match(html, /170,01–240,02/);
  assert.doesNotMatch(actionTag(html, 'start-bot'), / disabled/);
  await env.client.handleAction('stake', { dataset: { stakeMinor: '2500' } });
  assert.equal(env.client.currentStake(), 2500);
  assert.match(rangeTag(env.client.renderArena()), /value="2500"/);
  env.client.ensureStakeQuote();
  assert.equal([...env.timeouts.values()].filter(item => item.delay === 150).length, 0);
  assert.equal(sent.length, 1, 'Preset quotes already in state need no read request');
});

test('a late custom quote cannot replace a newer stake or a changed power snapshot', async () => {
  const replies = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: (url) => new Promise(resolve => replies.push({ url, resolve })) });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  env.client.selectStake(12345);
  const first = env.client.loadStakeQuote();
  env.client.selectStake(23456);
  const second = env.client.loadStakeQuote();
  assert.equal(replies.length, 2);
  const latest = customQuote(23456);
  latest.online.win_payout_minor = 43210;
  replies[1].resolve(reply(200, latest));
  await second;
  replies[0].resolve(reply(200, customQuote(12345)));
  await first;
  assert.equal(env.client.currentStake(), 23456);
  assert.equal(env.client.currentStakeQuotes().online.win_payout_minor, 43210);

  env.client.selectStake(34567);
  const oldPower = env.client.loadStakeQuote();
  const stronger = structuredClone(state);
  stronger.player.power = 120;
  env.client.acceptState(stronger);
  replies[2].resolve(reply(200, customQuote(34567, 100)));
  await oldPower;
  assert.equal(Boolean(env.client.currentStakeQuotes().bot), false);
  assert.match(actionTag(env.client.renderArena(), 'start-bot'), / disabled/);
  const freshPower = env.client.loadStakeQuote();
  replies[3].resolve(reply(200, customQuote(34567, 120)));
  await freshPower;
  assert.equal(env.client.currentStakeQuotes().bot.max_payout_minor, 24002);
});

test('a failed custom quote can be retried without changing the stake or posting a command', async () => {
  const sent = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: async (url, options) => {
    sent.push({ url, method: options.method });
    return sent.length === 1 ? reply(503, {}) : reply(200, customQuote(12345));
  } });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  env.client.selectStake(12345);
  await env.client.loadStakeQuote();
  let html = env.client.renderArena();
  assert.match(html, /data-action="retry-stake-quote"/);
  assert.match(actionTag(html, 'start-bot'), / disabled/);
  await env.client.handleAction('start-bot', { dataset: {} });
  assert.equal(sent.length, 1, 'A missing quote cannot initiate the paid command');
  await env.client.handleAction('retry-stake-quote');
  assert.equal(env.client.currentStake(), 12345);
  assert.equal(env.client.getPending(), null);
  assert.deepEqual(sent, Array.from({ length: 2 }, () => ({ url: '/api/v1/battle/quote?stake_minor=12345', method: 'GET' })));
  html = env.client.renderArena();
  assert.doesNotMatch(actionTag(html, 'start-bot'), / disabled/);
});

test('the full fractional balance is selectable and sent as an exact custom stake', async () => {
  const state = playerState();
  state.player.balance_minor = 2345;
  state.economy.stake_limits.max_minor = 2345;
  state.economy.first_free_battle_available = false;
  const sent = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: async (url, options) => {
    sent.push({ url, method: options.method, body: options.body && JSON.parse(options.body) });
    return reply(200, url.includes('/battle/quote?') ? customQuote(2345) : { state, result: {} });
  } });
  env.client.setState(state);
  await env.client.handleAction('stake-range', { value: '2345' });
  assert.match(rangeTag(env.client.renderArena()), /max="2345"/);
  assert.equal(env.client.currentStake(), 2345);
  await env.client.loadStakeQuote();
  await env.client.handleAction('start-bot', { dataset: {} });
  assert.deepEqual(sent, [
    { url: '/api/v1/battle/quote?stake_minor=2345', method: 'GET', body: undefined },
    { url: '/api/v1/battle/start', method: 'POST', body: { mode: 'bot', stake_minor: 2345, expected_power: 100 } },
  ]);
});

test('stake limits disable unaffordable presets and clamp saved choices after a balance change', async () => {
  const env = harness({ entries: { 'rooster.v1.stakeMinor': '35000' } });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  state.player.balance_minor = 7777;
  state.economy.stake_limits.max_minor = 7777;
  env.client.acceptState(state);
  assert.equal(env.client.currentStake(), 7777);
  assert.equal(env.session.get('rooster.v1.stakeMinor'), '7777');
  let html = env.client.renderArena();
  assert.match(html, /data-stake-minor="10000"[^>]* disabled/);
  assert.doesNotMatch(html, /data-stake-minor="5000"[^>]* disabled/);
  await env.client.handleAction('stake', { dataset: { stakeMinor: '10000' } });
  assert.equal(env.client.currentStake(), 7777);
  env.client.changeLanguage('en');
  assert.equal(env.client.currentStake(), 7777);
  assert.match(rangeTag(env.client.renderArena()), /value="7777"/);

  for (const balance of [0, 999]) {
    const poor = structuredClone(state);
    poor.player.balance_minor = balance;
    poor.economy.stake_limits.max_minor = balance;
    env.client.acceptState(poor);
    html = env.client.renderArena();
    assert.match(rangeTag(html), /min="1000"/);
    assert.match(rangeTag(html), /max="1000"/);
    assert.match(rangeTag(html), / disabled/);
    assert.match(actionTag(html, 'start-bot'), / disabled/);
    for (const stake of [1000, 2500, 5000, 10000]) assert.match(html, new RegExp(`data-stake-minor="${stake}"[^>]* disabled`));
    await env.client.handleAction('start-bot', { dataset: {} });
    await env.client.handleAction('queue-join', { dataset: {} });
    assert.equal(env.client.getPending(), null);
  }
});

test('balance clamping and language changes preserve a lost custom-start UUID and body', async () => {
  const state = playerState();
  state.economy.first_free_battle_available = false;
  const commands = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: async (url, options) => {
    if (url.includes('/battle/quote?')) return reply(200, customQuote(12345));
    commands.push({ url, body: options.body, key: options.headers['Idempotency-Key'] });
    if (commands.length === 1) throw new TypeError('Lost response after custom start');
    return reply(200, { state, result: {} });
  } });
  env.client.setState(state);
  env.client.selectStake(12345);
  await env.client.loadStakeQuote();
  await env.client.handleAction('start-bot', { dataset: {} });
  const original = JSON.stringify(env.client.getPending());
  const poorer = structuredClone(state);
  poorer.player.balance_minor = 2345;
  poorer.economy.stake_limits.max_minor = 2345;
  env.client.acceptState(poorer);
  assert.equal(env.client.currentStake(), 2345);
  env.client.changeLanguage('en');
  await env.client.handleAction('stake-range', { value: '1001' });
  assert.equal(JSON.stringify(env.client.getPending()), original);
  assert.equal(commands.length, 1);
  await env.client.sendPending();
  assert.deepEqual(commands[0], commands[1]);
  assert.equal(JSON.parse(commands[1].body).stake_minor, 12345);
});

test('a pending presence heartbeat keeps stake selection enabled without rewriting its command', async () => {
  const sent = [];
  let finishPresence;
  const state = playerState();
  state.economy.first_free_battle_available = false;
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: (url, options) => {
    sent.push({ url, method: options.method, body: options.body, key: options.headers['Idempotency-Key'] });
    if (url.endsWith('/presence')) return new Promise(resolve => { finishPresence = resolve; });
    return Promise.resolve(reply(200, customQuote(12345)));
  } });
  env.client.setState(state);
  const presence = env.client.mutate('/presence', {}, { quiet: true });
  const original = JSON.stringify(env.client.getPending());
  const saved = env.session.get('rooster.v1.pending');
  const presenceKey = env.client.getPending().key;
  assert.match(presenceKey, /^[0-9a-f-]{36}$/);

  await env.client.handleAction('stake-range', { value: '12345' });
  const html = env.client.renderArena();
  assert.equal(env.client.currentStake(), 12345);
  assert.match(rangeTag(html), /value="12345"/);
  assert.doesNotMatch(rangeTag(html), / disabled/);
  assert.match(actionTag(html, 'start-bot'), / disabled/);
  assert.equal(JSON.stringify(env.client.getPending()), original);
  assert.equal(env.session.get('rooster.v1.pending'), saved);
  env.client.ensureStakeQuote();
  assert.equal([...env.timeouts.values()].filter(item => item.delay === 150).length, 0);
  assert.equal(sent.length, 1, 'Selection must neither mutate nor fetch quotes during the heartbeat');

  finishPresence(reply(200, { state, result: {} }));
  await presence;
  assert.equal(env.client.currentStake(), 12345);
  assert.equal(env.client.getPending(), null);
  // The minimal DOM harness stubs render; invoke its quote scheduling step.
  env.client.ensureStakeQuote();
  assert.equal([...env.timeouts.values()].filter(item => item.delay === 150).length, 1);
  await env.client.loadStakeQuote();
  assert.equal(env.client.currentStakeQuotes().online.win_payout_minor, 23003);
  assert.doesNotMatch(actionTag(env.client.renderArena(), 'start-bot'), / disabled/);
  assert.deepEqual(sent, [
    { url: '/api/v1/presence', method: 'POST', body: '{}', key: presenceKey },
    { url: '/api/v1/battle/quote?stake_minor=12345', method: 'GET', body: undefined, key: undefined },
  ]);
});

test('mode help is local, preserves mode and stake, and closes by repeat, Escape or mode selection', async () => {
  const sent = [];
  const env = harness({ fetch: async (url) => { sent.push(url); throw new Error('Help must be local'); } });
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  await env.client.handleAction('stake', { dataset: { stakeMinor: '2500' } });
  const before = JSON.stringify(state);
  assert.match(env.client.renderArena(), /id="arena-mode-help" hidden/);
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  let html = env.client.renderArena();
  assert.match(html, /data-focus="help-online"[^>]*aria-expanded="true"/);
  assert.match(html, /aria-controls="arena-mode-help"/);
  assert.match(html, /47,5 <svg[^>]+data-icon="coin"/);
  assert.match(html, /без подбора по мощи или ставке/);
  assert.match(html, /data-action="start-bot"/);
  assert.doesNotMatch(html, /data-action="queue-join"/);
  assert.equal(env.session.get('rooster.v1.stakeMinor'), '2500');
  assert.equal(env.session.get('rooster.v1.arenaMode'), undefined);
  assert.equal(JSON.stringify(state), before);
  assert.equal(env.client.getPending(), null);
  assert.deepEqual(sent, []);

  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  assert.match(env.client.renderArena(), /id="arena-mode-help" hidden/);
  await env.client.handleAction('mode-help', { dataset: { mode: 'bot' } });
  let prevented = false;
  env.listeners.get('keydown')({ key: 'Escape', preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.match(env.client.renderArena(), /id="arena-mode-help" hidden/);
  assert.equal(env.document.activeElement, env.document.querySelector('[data-focus="help-bot"]'));
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  await env.client.handleAction('close-mode-help');
  assert.match(env.client.renderArena(), /id="arena-mode-help" hidden/);
  assert.equal(env.document.activeElement, env.document.querySelector('[data-focus="help-online"]'));
  await env.client.handleAction('mode-help', { dataset: { mode: 'bot' } });
  await env.client.handleAction('arena-mode', { dataset: { mode: 'online' } });
  html = env.client.renderArena();
  assert.match(html, /id="arena-mode-help" hidden/);
  assert.match(html, /Найти бой · 25 <svg[^>]+data-icon="coin"/);
  assert.deepEqual(sent, []);
});

test('open help follows authoritative quotes across polling, stake and language changes', async () => {
  const state = playerState();
  state.economy.first_free_battle_available = false;
  const refreshed = structuredClone(state);
  // Deliberately differ from the usual multiplier: the client must display the
  // quoted integer rather than recompute a payout from catalog or opponent data.
  refreshed.economy.online_quotes[1].win_payout_minor = 4721;
  const sent = [];
  const env = harness({ entries: { 'rooster.v1.token': 'active-session' }, fetch: async (url) => { sent.push(url); return reply(200, refreshed); } });
  env.client.setState(state);
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  await env.client.refreshState();
  await env.client.handleAction('stake', { dataset: { stakeMinor: '2500' } });
  let html = env.client.renderArena();
  assert.match(html, /data-focus="help-online"[^>]*aria-expanded="true"/);
  assert.match(html, /47,21 <svg[^>]+data-icon="coin"/);
  assert.match(html, /−25 <svg[^>]+data-icon="coin"/);
  env.client.changeLanguage('en');
  html = env.client.renderArena();
  assert.match(html, /data-focus="help-online"[^>]*aria-expanded="true"/);
  assert.match(html, /47\.21 <svg[^>]+data-icon="coin"/);
  assert.match(html, /regardless of power or stake/);
  assert.match(html, /data-action="start-bot"/);
  assert.deepEqual(sent, ['/api/v1/state']);
  assert.equal(env.client.getPending(), null);
});

test('first-free online help is collapsed initially and uses server quotes without starting a match', async () => {
  const env = harness();
  env.client.setState(playerState());
  const closed = env.client.renderArena();
  assert.match(closed, /id="arena-mode-help" hidden/);
  assert.doesNotMatch(closed, /class="arena-risk"/);
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  const opened = env.client.renderArena();
  assert.match(opened, /data-action="start-free"/);
  assert.match(opened, /data-focus="help-online"[^>]*aria-expanded="true"/);
  assert.match(opened, /19 <svg[^>]+data-icon="coin"/);
  assert.equal(env.client.getPending(), null);
});

test('a missing online quote disables matchmaking instead of inventing a payout', async () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  state.economy.online_quotes = [];
  env.client.setState(state);
  await env.client.handleAction('arena-mode', { dataset: { mode: 'online' } });
  await env.client.handleAction('mode-help', { dataset: { mode: 'online' } });
  const html = env.client.renderArena();
  assert.match(html, /data-action="queue-join"[^>]* disabled/);
  assert.match(html, /class="arena-quote-status ui-status" role="status">Рассчитываем выплату/);
  assert.doesNotMatch(html, /19 <svg[^>]+data-icon="coin"/);
});

test('finished results stay in history and the notice without a persistent Arena or queue card', () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  const result = { id: 'finished-history', mode: 'online', status: 'finished', opponent: { name: 'History opponent', is_bot: false }, wager: { stake_minor: 1000 }, result: { won: true, payout_minor: 1900, net_minor: 900, xp: 10 } };
  state.battle = result;
  state.history = [result];
  env.client.acceptState(state);
  let html = env.client.renderArena();
  assert.doesNotMatch(html, /last-battle-card|Последний бой/);
  assert.match(html, /history-list/);
  assert.match(html, /History opponent/);
  env.client.renderResultToast();
  assert.equal(env.document.querySelector('#battle-result-toast').hidden, false);
  env.client.closeResultToast();
  assert.match(env.client.renderArena(), /History opponent/);

  state.queue = { joined_at: 0, expires_at: 120, stake_minor: 1000 };
  html = env.client.renderArena();
  assert.match(html, /data-action="queue-leave"/);
  assert.doesNotMatch(html, /last-battle-card|Последний бой/);
  assert.match(env.client.renderHistory(), /History opponent/);
  state.queue = null;
  state.battle = null;
  const restored = harness({ entries: Object.fromEntries(env.session) });
  restored.client.acceptState(structuredClone(state));
  html = restored.client.renderArena();
  assert.doesNotMatch(html, /last-battle-card|Последний бой/);
  assert.match(html, /History opponent/);
  assert.equal(vm.runInContext('resultToast', restored.context), null);
});

test('first free battle is the primary action and no automatic upgrade is offered', () => {
  const env = harness();
  const state = playerState();
  env.client.setState(state);
  const html = env.client.renderArena();
  const hero = html.search(/class="hero(?:\s|")/);
  assert.ok(hero >= 0 && html.indexOf('data-action="start-free"') < hero);
  assert.match(html, /15 монет при любом исходе/);
  assert.match(html, /90 нажатий/);
  const result = env.client.renderResult({ mode: 'bot', opponent: { name: 'Бот', is_bot: true }, wager: { stake_minor: 0 }, result: { won: false, payout_minor: 1500, net_minor: 1500, xp: 5 } });
  assert.match(result, /Итог: \+15/);
  assert.doesNotMatch(result, /go-gear|upgrade|Усилить/);
});

test('Arena uses current appearance while bot and online fighters use their battle snapshots', () => {
  const env = harness();
  const state = playerState();
  state.player.breed_id = 'ember';
  state.player.gear = { helmet: 10, armor: 10, sword: 10 };
  env.client.setState(state);
  assert.match(env.client.renderArena(), /data-breed="ember"/);
  const battle = {
    id: 'frozen-fighter-art', status: 'active', rules_version: 'v8',
    starts_at: Date.now() / 1000 - 1, ends_at: Date.now() / 1000 + 9,
    tap_cap: 90, wager: { stake_minor: 1000, win_payout_minor: 2200 },
    you: { name: 'Player', breed_id: 'yard', gear: { helmet: 0, armor: 1, sword: 3 }, power: 120, taps: 0 },
    opponent: { name: 'Opponent', breed_id: 'storm', gear: { helmet: 5, armor: 8, sword: 10 }, power: 240, taps: 0 },
  };
  for (const mode of ['bot', 'online']) {
    battle.mode = mode;
    battle.opponent.is_bot = mode === 'bot';
    const html = env.client.renderBattle(battle);
    const art = html.match(/<svg\b[^>]*\bdata-breed="[^"]+"[^>]*>/g);
    assert.equal(art.length, 2);
    assert.match(art[0], /data-breed="yard"/);
    assert.match(art[1], /data-breed="storm"/);
    assert.doesNotMatch(html, /data-breed="ember"/);
    assert.match(html, /data-fighter="you"/);
    assert.match(html, /data-fighter="opponent"/);
  }
  assert.deepEqual(state.player.gear, { helmet: 10, armor: 10, sword: 10 });
});

test('all 80 clicks can be submitted in one batch with no client pace limit', async () => {
  const sent = [];
  const state = playerState();
  state.battle = { id: 'fast-taps', status: 'active', starts_at: Date.now() / 1000 - 1, ends_at: Date.now() / 1000 + 9, tap_cap: 80, you: { taps: 0 }, opponent: { taps: 0 } };
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, body: JSON.parse(options.body) });
    state.battle.you.taps = 80;
    return reply(200, { state, result: { accepted: 80 } });
  } });
  env.client.setState(state);
  for (let i = 0; i < 80; i += 1) await env.client.handleAction('battle-tap', { dataset: {} });
  await env.intervals.find((item) => item.delay === 400).fn();
  assert.deepEqual(sent, [{ url: '/api/v1/battle/tap', body: { battle_id: 'fast-taps', taps: 80 } }]);
});

test('lost tap response keeps input live and automatically replays the same batch before queued clicks', async () => {
  const state = playerState();
  state.battle = { id: 'retry-taps', status: 'active', starts_at: Date.now() / 1000 - 1,
    ends_at: Date.now() / 1000 + 9, tap_cap: 90, you: { taps: 0 }, opponent: { taps: 0 } };
  const sent = [];
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, key: options.headers['Idempotency-Key'], body: options.body });
    if (sent.length === 1) throw new TypeError('Response lost after commit');
    const next = structuredClone(state);
    next.battle.you.taps = sent.length === 2 ? 1 : 3;
    return reply(200, { state: next, result: {} });
  } });
  env.client.setState(state);
  const flush = env.intervals.find(item => item.delay === 400).fn;
  await env.client.handleAction('battle-tap');
  await flush();
  await env.client.handleAction('battle-tap');
  await env.client.handleAction('battle-tap');
  assert.equal(env.client.getBattleBuffer().count, 2, 'A lost reply must not disable local input');
  assert.equal(env.client.getState().battle.you.taps, 0, 'Only the server may confirm taps');
  const retry = [...env.timeouts.values()].find(item => item.delay === 500);
  assert.ok(retry, 'Transient tap failures must schedule an automatic retry');
  await retry.fn();
  assert.deepEqual(sent[0], sent[1]);
  await flush();
  assert.equal(JSON.parse(sent[2].body).taps, 2);
  assert.notEqual(sent[2].key, sent[0].key);
  assert.equal(env.client.getState().battle.you.taps, 3);
});

test('a stuck tap request times out before the whole battle and retains its UUID', async () => {
  const state = playerState();
  state.battle = { id: 'slow-taps', status: 'active', starts_at: Date.now() / 1000 - 1,
    ends_at: Date.now() / 1000 + 9, tap_cap: 90, you: { taps: 0 }, opponent: { taps: 0 } };
  const env = harness({ fetch: (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject({ name: 'AbortError' }));
  }) });
  env.client.setState(state);
  const sending = env.client.mutate('/battle/tap', { battle_id: 'slow-taps', taps: 1 }, { quiet: true });
  const key = env.client.getPending().key;
  const timeout = [...env.timeouts.values()].find(item => item.delay === 2500);
  assert.ok(timeout, 'Battle requests must not wait for the general 12-second timeout');
  timeout.fn();
  assert.equal(await sending, false);
  assert.equal(env.client.getPending().key, key);
  assert.ok([...env.timeouts.values()].some(item => item.delay === 500));
});

test('a failed presence during combat also recovers without disabling taps', async () => {
  const state = playerState();
  state.battle = { id: 'presence-taps', status: 'active', starts_at: Date.now() / 1000 - 1,
    ends_at: Date.now() / 1000 + 9, tap_cap: 90, you: { taps: 0 }, opponent: { taps: 0 } };
  const env = harness({ fetch: async () => { throw new TypeError('offline'); } });
  env.client.setState(state);
  await env.client.mutate('/presence', {}, { quiet: true });
  const key = env.client.getPending().key;
  await env.client.handleAction('battle-tap');
  assert.equal(env.client.getBattleBuffer().count, 1);
  for (const delay of [500, 1000, 2000, 2000]) {
    const timer = [...env.timeouts.values()].find(item => item.delay === delay);
    assert.ok(timer, `Missing bounded retry after ${delay}ms`);
    await timer.fn();
    assert.equal(env.client.getPending().key, key);
    assert.equal(env.client.getPending().path, '/presence');
  }
});

test('combat recovery does not automatically repeat economic commands', async () => {
  const env = harness({ fetch: async () => { throw new TypeError('offline'); } });
  env.client.setState(playerState());
  await env.client.mutate('/battle/start', { mode: 'bot', stake_minor: 1000 });
  assert.ok(env.client.getPending());
  assert.equal([...env.timeouts.values()].some(item => [500, 1000, 2000].includes(item.delay)), false);
});

test('hidden combat pauses retries and resumes the same pending batch when visible', async () => {
  const state = playerState();
  state.battle = { id: 'resume-taps', status: 'active', starts_at: Date.now() / 1000 - 1,
    ends_at: Date.now() / 1000 + 9, tap_cap: 90, you: { taps: 0 }, opponent: { taps: 0 } };
  const sent = [];
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, key: options.headers['Idempotency-Key'], body: options.body });
    if (sent.length === 1) throw new TypeError('offline');
    return reply(200, { state, result: {} });
  } });
  env.client.setState(state);
  await env.client.mutate('/battle/tap', { battle_id: 'resume-taps', taps: 1 }, { quiet: true });
  env.document.hidden = true;
  await [...env.timeouts.values()].find(item => item.delay === 500).fn();
  assert.equal(sent.length, 1);
  env.document.hidden = false;
  env.listeners.get('visibilitychange')();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(env.client.getPending(), null);
});

test('battle settlement clears buffered taps after recovery and prevents late input', async () => {
  const state = playerState();
  state.battle = { id: 'ended-taps', status: 'active', starts_at: Date.now() / 1000 - 1,
    ends_at: Date.now() / 1000 + 9, tap_cap: 90, you: { taps: 0 }, opponent: { taps: 0 } };
  let calls = 0;
  const env = harness({ fetch: async () => {
    if (++calls === 1) throw new TypeError('offline');
    const next = structuredClone(state);
    next.battle.status = 'finished';
    return reply(200, { state: next, result: { accepted: 1 } });
  } });
  env.client.setState(state);
  await env.client.mutate('/battle/tap', { battle_id: 'ended-taps', taps: 1 }, { quiet: true });
  await env.client.handleAction('battle-tap');
  assert.equal(env.client.getBattleBuffer().count, 1);
  await [...env.timeouts.values()].find(item => item.delay === 500).fn();
  await env.client.handleAction('battle-tap');
  assert.equal(env.client.getBattleBuffer().count, 0);
  await env.intervals.find(item => item.delay === 400).fn();
  assert.equal(calls, 2, 'No extra batch may be sent after settlement');
});

test('coin display preserves minor-unit precision without redundant decimals', () => {
  const env = harness();
  assert.equal(env.client.money(1099), '10,99');
  assert.equal(env.client.money(1000), '10');
  assert.equal(env.client.signedMoney(-101), '−1,01');
});

test('legacy paid and practice battles make no new fixed-reward promises', () => {
  const env = harness();
  env.client.setState(playerState());
  for (const mode of ['bot', 'practice']) {
    const html = env.client.renderBattle({ rules_version: 'v2', mode, starts_at: 0, ends_at: 10, tap_cap: 60, wager: { stake_minor: mode === 'bot' ? 3000 : 0, win_payout_minor: 8500 }, you: { name: 'Игрок', power: 100, taps: 0 }, opponent: { name: 'Бот', power: 100, taps: 0, is_bot: true } });
    assert.match(html, /Бой начат по прежним правилам/);
    assert.doesNotMatch(html, /при любом исходе|При поражении: 0/);
  }
});

test('late auth renewal cannot roll state back after a committed purchase', async () => {
  const oldState = playerState('tg:42');
  const newState = playerState('tg:42');
  newState.player.balance_minor = 34000;
  let finishSlowAuth;
  let notifyAuthStarted;
  const authStarted = new Promise((resolve) => { notifyAuthStarted = resolve; });
  let authCalls = 0;
  let stateCalls = 0;
  let gearCalls = 0;
  const env = harness({ initData: 'launch', entries: { 'rooster.v1.token': 'expired' }, fetch: async (url) => {
    if (url.endsWith('/state')) return ++stateCalls === 1 ? reply(401, {}) : reply(200, newState);
    if (url.endsWith('/auth/telegram')) {
      if (++authCalls === 1) {
        notifyAuthStarted();
        return new Promise((resolve) => { finishSlowAuth = resolve; });
      }
      return reply(200, { token: 'renewed', state: oldState });
    }
    if (url.endsWith('/gear/upgrade')) return ++gearCalls === 1 ? reply(401, {}) : reply(200, { result: {}, state: newState });
    throw new Error('Unexpected route');
  } });
  env.client.setState(oldState);
  const refresh = env.client.refreshState();
  await authStarted;
  assert.equal(await env.client.mutate('/gear/upgrade', { slot: 'helmet' }), true);
  assert.equal(env.client.getState().player.balance_minor, 34000);
  finishSlowAuth(reply(200, { token: 'renewed-again', state: oldState }));
  await refresh;
  assert.equal(env.client.getState().player.balance_minor, 34000);
});

test('preparation becomes a 10-second battle locally, with legacy 15-second timing preserved', () => {
  for (const duration of [10, 15]) {
    const env = harness();
    const battle = { status: 'active', starts_at: 102, ends_at: 102 + duration, tap_cap: 60, you: { taps: 0 } };
    env.client.setState({ ...playerState(), battle });
    const element = (selector) => env.document.querySelector(selector);

    env.client.setServerNow(100.05);
    env.client.updateTimers();
    assert.equal(element('#battle-seconds').textContent, '2');
    assert.equal(element('#battle-phase').textContent, 'ПРИГОТОВЬСЯ');
    assert.equal(element('[data-action="battle-tap"]').disabled, true);
    assert.match(element('#battle-caption').textContent, /Ставка и соперник зафиксированы/);

    // No state request or rerender: the UI enables taps at the start boundary.
    env.client.setServerNow(102.05);
    env.client.updateTimers();
    assert.equal(element('#battle-seconds').textContent, String(duration));
    assert.equal(element('#battle-phase').textContent, 'БОЙ ИДЁТ');
    assert.equal(element('[data-action="battle-tap"]').disabled, false);
    assert.equal(element('#battle-caption').textContent, 'Принятые тапы повышают шанс победы.');

    env.client.setServerNow(battle.ends_at + 0.1);
    env.client.updateTimers();
    assert.equal(element('#battle-seconds').textContent, '0');
    assert.equal(element('#battle-phase').textContent, 'БОЙ ЗАВЕРШЁН');
    assert.equal(element('[data-action="battle-tap"]').disabled, true);
    assert.equal(element('#battle-caption').textContent, 'Бой завершён. Ждём результат с сервера…');
  }
});

test('language can change before authentication and persists across a new document', () => {
  const env = harness();
  assert.equal(env.client.getLanguage(), 'ru');
  assert.equal(env.document.documentElement.lang, 'ru');
  env.listeners.get('click')({ target: { closest: (selector) => selector === '[data-language]' ? { dataset: { language: 'en' } } : null } });
  assert.equal(env.client.getState(), null);
  assert.equal(env.client.getLanguage(), 'en');
  assert.equal(env.document.documentElement.lang, 'en');
  assert.equal(env.document.title, 'Rooster Club · The Fighting Yard');
  assert.equal(env.local.get(LANGUAGE_STORAGE_KEY), 'en');
  const reopened = harness({ localEntries: Object.fromEntries(env.local) });
  assert.equal(reopened.client.getLanguage(), 'en');
  assert.equal(reopened.client.money(1099), '10.99');
  env.client.changeLanguage('ru');
  assert.equal(env.client.money(1099), '10,99');
});

test('all English screen renderers translate catalog IDs while preserving actual names', () => {
  const env = harness({ localEntries: { [LANGUAGE_STORAGE_KEY]: 'en' } });
  const state = playerState();
  state.player.name = 'Мария <script>alert(1)</script>';
  state.player.gear = { armor: 1, helmet: 2, sword: 3 };
  state.catalog.slots = [
    { id: 'armor', name: 'Броня', power_per_level: 12 },
    { id: 'helmet', name: 'Шлем', power_per_level: 8 },
    { id: 'sword', name: 'Меч', power_per_level: 15 },
  ];
  state.catalog.breeds[0].description = 'Серверное русское описание';
  state.economy.upgrade_costs_minor = { armor: 1000, helmet: 1000, sword: 1000 };
  state.economy.daily_available = false;
  state.economy.next_daily_at = Date.UTC(2026, 8, 23, 12) / 1000;
  const result = {
    id: 'last', mode: 'bot', status: 'finished', win_probability: .5,
    opponent: { name: 'Клювдиатор', is_bot: true }, wager: { stake_minor: 1000 },
    result: { won: true, payout_minor: 1900, net_minor: 900, xp: 10 },
  };
  state.history = [result];
  env.client.setState(state);
  env.client.setConfig({ bot_username: 'rooster_test_bot' });
  env.client.setLeaderboard({ power: [state.player], pvp_wins: [state.player] });
  for (const html of [env.client.renderArena(), env.client.renderGear(), env.client.renderRoost(), env.client.renderLeaderboard(), env.client.renderResult(result)]) {
    assert.doesNotMatch(html.replaceAll('Мария', ''), /[А-Яа-яЁё]/, 'Only an actual player name may remain Russian');
    assert.doesNotMatch(html, /<script>/, 'Server names must be escaped');
  }
  assert.match(env.client.renderLeaderboard(), /Мария &lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.match(env.client.renderGear(), /Armor/);
  assert.match(env.client.renderGear(), /Helmet/);
  assert.match(env.client.renderGear(), /Sword/);
  assert.equal(env.client.fighterName({ name: 'Клювдиатор', is_bot: false }), 'Клювдиатор');
  assert.notEqual(env.client.fighterName({ name: 'Клювдиатор', is_bot: true }), 'Клювдиатор');
  assert.match(env.client.renderResult(result), /Net: \+9 <svg[^>]+data-icon="coin"/);
  assert.match(env.client.renderResult(result), /Payout 19/);
});

test('Roost explains both inviter rewards using localized server amounts and battle requirement', () => {
  const env = harness();
  const state = playerState('tg:42');
  state.catalog.referral = { signup_reward_minor: 43210, battle_reward_minor: 76543, battles_required: 7 };
  env.client.setState(state);
  env.client.setConfig({ bot_username: 'rooster_test_bot' });
  const ru = env.client.renderRoost();
  assert.match(ru, /Ты получишь 432,1 монет, когда новый друг впервые зайдёт в игру по твоей ссылке, и ещё 765,43 монет после его 7 боёв/);
  assert.match(ru, /Учитываются бесплатный бой, тренировки и онлайн/);
  env.client.changeLanguage('en');
  const en = env.client.renderRoost();
  assert.match(en, /You receive 432\.1 coins when a new friend first joins the game through your link, plus 765\.43 more after they complete 7 battles/);
  assert.match(en, /The free battle, training and online battles all count/);
  assert.doesNotMatch(en, /you both|ranked battles/);
});

test('Roost payout history retains server order, zero payouts and escaped names across refresh and locale changes', () => {
  const env = harness();
  const state = playerState('tg:42');
  const timestamp = Date.UTC(2026, 8, 26, 12) / 1000;
  const events = [
    { id: 44, reason: 'battle_reward', amount_minor: 0, created_at: timestamp, friend_name: null },
    { id: 43, reason: 'referral', amount_minor: 30000, created_at: timestamp - 60, friend_name: '<script>friend</script>' },
    { id: 42, reason: 'referral_signup', amount_minor: 30000, created_at: timestamp - 120, friend_name: '<script>friend</script>' },
    { id: 41, reason: 'referral', amount_minor: 15000, created_at: timestamp - 180, friend_name: null },
  ];
  env.client.setState(state);
  env.client.setConfig({ bot_username: 'rooster_test_bot' });
  assert.match(env.client.renderRoost(), /Здесь появятся бонусы за друзей и выплаты за бои/);
  state.reward_events = events;
  const ru = env.client.renderRoost();
  assert.ok(ru.indexOf('roost-referral-title') < ru.indexOf('roost-events-title'));
  assert.deepEqual([...ru.matchAll(/data-reward-event="(\d+)"/g)].map(match => Number(match[1])), [44, 43, 42, 41]);
  assert.match(ru, /Выплата за бой/);
  assert.match(ru, /Бонус за бои по приглашению/);
  assert.match(ru, /Бонус за приглашение/);
  assert.match(ru, /<span>0<\/span>/);
  assert.match(ru, /<span>\+300<\/span>/);
  assert.match(ru, /&lt;script&gt;friend&lt;\/script&gt;/);
  assert.doesNotMatch(ru, /<script>|\bnull\b/);
  assert.ok(ru.includes(env.client.formatDate(timestamp)));
  env.client.setState(structuredClone(state));
  assert.equal(env.client.renderRoost(), ru, 'Repeated snapshots must not append events');
  env.client.changeLanguage('en');
  const en = env.client.renderRoost();
  assert.match(en, /Payout history/);
  assert.match(en, /Battle payout/);
  assert.match(en, /Referral battle bonus/);
  assert.match(en, /Invitation bonus/);
  assert.match(en, /Winning payouts include the stake/);
  assert.ok(en.includes(env.client.formatDate(timestamp)));
  assert.equal((en.match(/data-reward-event=/g) || []).length, 4);
});

test('changing language retains buffered taps and sends no command of its own', async () => {
  const sent = [];
  const state = playerState();
  state.battle = { id: 'language-during-battle', mode: 'bot', status: 'active', starts_at: Date.now() / 1000 - 1, ends_at: Date.now() / 1000 + 9, tap_cap: 80, you: { name: 'Игрок', taps: 0, power: 100 }, opponent: { name: 'Клювдиатор', is_bot: true, taps: 0, power: 100 }, wager: { stake_minor: 0, win_payout_minor: 1500 } };
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, body: JSON.parse(options.body) });
    state.battle.you.taps = 80;
    return reply(200, { state, result: { accepted: 80 } });
  } });
  env.client.setState(state);
  for (let i = 0; i < 6; i += 1) await env.client.handleAction('battle-tap', { dataset: {} });
  const before = JSON.stringify(state);
  env.client.changeLanguage('en');
  assert.equal(JSON.stringify(state), before);
  assert.equal(env.client.getState(), state);
  assert.equal(env.client.getBattleBuffer().count, 6);
  assert.equal(env.client.getBattleBuffer().battleId, state.battle.id);
  assert.equal(sent.length, 0);
  assert.match(env.client.renderBattle(state.battle), /Your taps/);
  for (let i = 6; i < 80; i += 1) await env.client.handleAction('battle-tap', { dataset: {} });
  await env.intervals.find((item) => item.delay === 400).fn();
  assert.deepEqual(sent, [{ url: '/api/v1/battle/tap', body: { battle_id: 'language-during-battle', taps: 80 } }]);
});

test('an unresolved purchase keeps its exact request across a language change', async () => {
  const sent = [];
  const env = harness({ fetch: async (url, options) => {
    sent.push({ url, body: options.body, key: options.headers['Idempotency-Key'] });
    if (sent.length === 1) throw new TypeError('Lost response');
    return reply(200, { state: playerState(), result: {} });
  } });
  env.client.setState(playerState());
  await env.client.mutate('/gear/upgrade', { slot: 'sword' }, { messageKey: 'notice.gearUpgraded' });
  const pending = JSON.stringify(env.client.getPending());
  env.client.changeLanguage('en');
  assert.equal(JSON.stringify(env.client.getPending()), pending);
  assert.equal(sent.length, 1);
  await env.client.sendPending();
  assert.deepEqual(sent[0], sent[1]);
  assert.match(env.client.messageText(env.context.lastNotice.message), /^Gear upgraded\./);
});

test('legacy pending success text and visible errors follow the selected language', async () => {
  const command = { path: '/claim/daily', body: {}, key: 'legacy-command', owner: 'dev:tester', attempts: 1, message: 'Ежедневная награда получена.' };
  const env = harness({
    entries: { 'rooster.v1.pending': JSON.stringify(command) },
    localEntries: { [LANGUAGE_STORAGE_KEY]: 'en' },
    fetch: async () => reply(200, { state: playerState(), result: {} }),
  });
  env.client.setState(playerState());
  await env.client.sendPending();
  assert.match(env.client.messageText(env.context.lastNotice.message), /daily reward.*wallet/);
  env.client.actualShowNotice({ key: 'notice.retrySafe', params: { message: env.client.errorMessage(env.client.localizedError('error.offline')) } }, { error: true, retry: true });
  const notice = env.document.querySelector('#notice-stack');
  assert.doesNotMatch(notice.innerHTML, /[А-Яа-яЁё]/);
  assert.match(notice.innerHTML, /Cannot reach the yard/);
  assert.match(notice.innerHTML, /Retry/);
  env.client.changeLanguage('ru');
  assert.match(notice.innerHTML, /Повторить/);
  assert.match(notice.innerHTML, /[А-Яа-яЁё]/);
  env.client.changeLanguage('en');
  assert.doesNotMatch(notice.innerHTML, /[А-Яа-яЁё]/);
});

test('server errors use stable codes and safely ignore untrusted localized prose', async () => {
  const env = harness({ localEntries: { [LANGUAGE_STORAGE_KEY]: 'en' }, fetch: async () => reply(400, { error: { code: 'insufficient_funds', message: 'Русский текст <script>secret</script>' } }) });
  await assert.rejects(env.client.request('/gear/upgrade', { body: { slot: 'armor' } }), (error) => {
    assert.equal(error.code, 'insufficient_funds');
    assert.match(error.message, /coins/);
    assert.doesNotMatch(error.message, /Русский|script|secret/);
    return true;
  });
  assert.doesNotMatch(env.client.messageText(env.client.errorMessage({ code: 'future_error' })), /[А-Яа-яЁё]/);
});

test('breed cards describe multipliers and accepted server odds appear during battle', () => {
  const env = harness();
  const state = playerState();
  env.client.setState(state);
  env.client.changeLanguage('en');
  assert.match(env.client.renderGear(), /×1 total power/);
  const battle = { id: 'live-odds', status: 'active', mode: 'bot', starts_at: 0, ends_at: 10, tap_cap: 90, current_win_probability: .55, you: { name: 'Me', power: 100, taps: 54 }, opponent: { name: 'Bot', is_bot: true, power: 100, taps: 0 }, wager: { stake_minor: 1000, win_payout_minor: 1800 } };
  const html = env.client.renderBattle(battle);
  assert.match(html, /Current win chance/);
  assert.match(html, /55%/);
  assert.match(html, /up to 90/);
  assert.match(html, /54 \/ 90/);
  // The same live server values remain readable for PvP; explanatory help is
  // no longer part of the compact battle layout.
  battle.opponent.is_bot = false;
  battle.opponent.taps = 12;
  battle.current_win_probability = .425;
  const pvp = env.client.renderBattle(battle);
  assert.match(pvp, /42.5%/);
  assert.match(pvp, /Player/);
  assert.match(pvp, /Opponent: <strong>12<\/strong>/);
  assert.doesNotMatch(pvp, /class="battle-help/);
});

test('result notice closes manually or at its original deadline, without replay on poll or reload', async () => {
  const env = harness();
  const state = playerState();
  state.battle = { id: 'finished-toast', status: 'finished', result: { won: true, payout_minor: 1800, net_minor: 800, xp: 25 } };
  env.client.acceptState(state);
  env.client.renderResultToast();
  const toast = env.document.querySelector('#battle-result-toast');
  assert.equal(toast.hidden, false);
  const deadline = vm.runInContext('resultToastUntil', env.context);
  env.client.acceptState(structuredClone(state));
  env.client.changeLanguage('en');
  assert.equal(vm.runInContext('resultToastUntil', env.context), deadline);
  await env.client.handleAction('close-result');
  assert.equal(toast.hidden, true);
  env.client.acceptState(structuredClone(state));
  env.client.renderResultToast();
  assert.equal(toast.hidden, true);
  const restored = harness({ entries: Object.fromEntries(env.session) });
  restored.client.acceptState(structuredClone(state));
  assert.equal(vm.runInContext('resultToast', restored.context), null);
  state.battle.id = 'second-toast';
  env.client.acceptState(state);
  env.client.renderResultToast();
  assert.equal(toast.hidden, false);
  vm.runInContext('resultToastUntil = Date.now() - 1; updateResultToastTimer();', env.context);
  assert.equal(toast.hidden, true);
  env.client.acceptState(structuredClone(state));
  assert.equal(vm.runInContext('resultToast', env.context), null);
});

test('online result shows accepted taps and server odds, remains readable and reopens from history locally', async () => {
  const env = harness({ localEntries: { 'rooster.v1.guideSeen.v1': '1' } });
  const state = playerState();
  const battle = {
    id: 'online-analysis', mode: 'online', status: 'finished',
    you: { name: '<you>', power: 100, taps: 90 },
    opponent: { name: '<opponent>', power: 100, taps: 0, is_bot: false },
    wager: { stake_minor: 1000, win_payout_minor: 2200 },
    result: { won: true, payout_minor: 2200, net_minor: 1200, xp: 25 },
    tap_analysis: {
      you: { taps: 90, initial_probability: 0.5, final_probability: 6/11, change: 6/11 - 0.5 },
      opponent: { taps: 0, initial_probability: 0.5, final_probability: 5/11, change: 5/11 - 0.5 },
    },
  };
  state.battle = battle;
  state.history = [battle];
  env.client.acceptState(state);
  env.client.renderResultToast();
  const toast = env.document.querySelector('#battle-result-toast');
  assert.match(toast.innerHTML, /90<\/td>/);
  assert.match(toast.innerHTML, /54,55%/);
  assert.match(toast.innerHTML, /45,45%/);
  assert.match(toast.innerHTML, /\+4,55 п.п./);
  assert.match(toast.innerHTML, /−4,55 п.п./);
  assert.match(toast.innerHTML, /&lt;opponent&gt;/);
  assert.equal(vm.runInContext('resultToastUntil', env.context), Infinity);
  assert.doesNotMatch(toast.innerHTML, /result-close-timer/);
  assert.match(env.client.renderHistory(), /<button[^>]*data-action="open-result"/);
  await env.client.handleAction('close-result');
  assert.equal(toast.hidden, true);
  env.client.acceptState(structuredClone(state));
  assert.equal(vm.runInContext('resultToast', env.context), null);
  await env.client.handleAction('open-result', { dataset: { battleId: battle.id } });
  assert.equal(toast.hidden, false);
  const original = toast.innerHTML;
  env.client.acceptState(structuredClone(state));
  env.client.renderResultToast();
  assert.equal(toast.innerHTML, original);
  env.client.changeLanguage('en');
  env.client.renderResultToast();
  assert.match(toast.innerHTML, /How taps changed the odds/);
  assert.match(toast.innerHTML, /54.55%/);
  env.listeners.get('keydown')({ key: 'Escape', preventDefault() {} });
  assert.equal(toast.hidden, true);
  await env.client.handleAction('open-result', { dataset: { battleId: 'unknown' } });
  assert.equal(toast.hidden, true);
  await env.client.handleAction('open-result', { dataset: { battleId: battle.id } });
  const active = structuredClone(state);
  active.battle = { ...battle, id: 'new-fight', status: 'active', result: null };
  env.client.acceptState(active);
  assert.equal(toast.hidden, true);
});
