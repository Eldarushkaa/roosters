/** Transport regressions with a minimal DOM; real layout is browser-tested. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';
import test from 'node:test';
import vm from 'node:vm';
import { createI18n, botNameKeys, LANGUAGE_STORAGE_KEY } from '../i18n.mjs';

const source = (await readFile(new URL('../app.js', import.meta.url), 'utf8'))
  .replace(/^import .* from '\.\/i18n\.mjs';\n/m, '')
  .replace(/^import .* from '\.\/environment\.mjs';\n/m, '')
  .replace(/\nboot\(\);\s*$/, '\n');
const playerState = (id = 'dev:tester') => ({
  server_time: Date.now() / 1000,
  player: { id, name: 'Игрок', is_dev: id.startsWith('dev:'), balance_minor: 42000, power: 100, breed_id: 'yard', owned_breeds: ['yard'], level: 1, xp_in_level: 0, xp_to_next: 100, wins: 0, losses: 0, battles: 0, pvp_wins: 0, gear: {} },
  economy: { first_free_battle_available: true, passive_available_minor: 0, daily_reward_minor: 17000, daily_available: true, next_daily_at: 0, upgrade_costs_minor: {}, bot_quotes: [1000, 2500, 5000, 10000].map((stake_minor) => ({ stake_minor, min_payout_minor: stake_minor * 1.5, max_payout_minor: stake_minor * 2.5 })) },
  catalog: { breeds: [{ id: 'yard', name: 'Дворовый', price_minor: 0, power_multiplier: 1, power_multiplier_percent: 100, color: '#f3ad55' }], slots: [], battle: { duration: 10, roulette_duration: 2, tap_cap: 80, stakes_minor: [1000, 2500, 5000, 10000], bot_power_min_ratio: 0.7, bot_power_max_ratio: 1.4, bot_rtp_min: 0.9, bot_rtp_max: 1.05, pvp_pool_return: 0.95, free_reward_minor: 1500 } },
  presence: { online: 0, searching: 0, development_online: 1 }, history: [], queue: null, battle: null,
});
const reply = (status, payload) => ({ status, ok: status >= 200 && status < 300, json: async () => payload });

function harness({ fetch, entries = {}, localEntries = {}, initData = '' } = {}) {
  const session = new Map(Object.entries(entries));
  const local = new Map(Object.entries(localEntries));
  const store = (data) => ({ getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, String(value)), removeItem: (key) => data.delete(key) });
  const elements = new Map();
  const element = () => ({ hidden: false, dataset: {}, style: {}, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, replaceChildren() {}, querySelectorAll: () => [], setAttribute() {}, removeAttribute() {} });
  const app = { initData, ready() {}, expand() {} };
  const intervals = [];
  const listeners = new Map();
  const sandbox = {
    createAppEnvironment: () => ({ telegram: app, start() { app.ready(); app.expand(); }, onResume(callback) { listeners.set('visibilitychange', callback); } }),
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
    setTimeout: () => 1, clearTimeout() {}, setInterval(fn, delay) { intervals.push({ fn, delay }); return intervals.length; },
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
  return { ...sandbox, session, local, app, context: sandbox, intervals, listeners };
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

test('arena discloses wagers and renders no old training, energy or second currency', () => {
  const env = harness();
  const state = playerState();
  state.economy.first_free_battle_available = false;
  env.client.setState(state);
  env.client.setConfig({ bot_username: '' });
  const arena = env.client.renderArena();
  assert.match(arena, /Бой длится 10 секунд/);
  assert.match(arena, /data-action="start-bot"/);
  assert.doesNotMatch(arena, /data-action="queue-join"/);
  assert.match(arena, /data-action="arena-mode" data-mode="online"/);
  assert.match(arena, /15–25/);
  assert.match(arena, /При поражении выплата 0/);
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
  const html = env.client.renderArena();
  assert.match(html, /data-action="queue-join"/);
  assert.doesNotMatch(html, /data-action="start-bot"/);
  assert.match(html, /47,5 ✦/);
  assert.match(html, /−25 ✦/);
  assert.equal(env.session.get('rooster.v1.arenaMode'), 'online');
  const restored = harness({ entries: Object.fromEntries(env.session) });
  restored.client.setState(state);
  assert.match(restored.client.renderArena(), /Найти бой · 25 ✦/);
});

test('first free battle is the primary action and no automatic upgrade is offered', () => {
  const env = harness();
  const state = playerState();
  env.client.setState(state);
  const html = env.client.renderArena();
  assert.ok(html.indexOf('data-action="start-free"') < html.indexOf('class="hero"'));
  assert.match(html, /15 монет при любом исходе/);
  assert.match(html, /80 нажатий/);
  const result = env.client.renderResult({ mode: 'bot', opponent: { name: 'Бот', is_bot: true }, wager: { stake_minor: 0 }, result: { won: false, payout_minor: 1500, net_minor: 1500, xp: 5 } });
  assert.match(result, /Итог: \+15/);
  assert.doesNotMatch(result, /go-gear|upgrade|Усилить/);
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

    // No state request or rerender: the UI enables taps at the start boundary.
    env.client.setServerNow(102.05);
    env.client.updateTimers();
    assert.equal(element('#battle-seconds').textContent, String(duration));
    assert.equal(element('#battle-phase').textContent, 'БОЙ ИДЁТ');
    assert.equal(element('[data-action="battle-tap"]').disabled, false);

    env.client.setServerNow(battle.ends_at + 0.1);
    env.client.updateTimers();
    assert.equal(element('#battle-seconds').textContent, '0');
    assert.equal(element('#battle-phase').textContent, 'БОЙ ЗАВЕРШЁН');
    assert.equal(element('[data-action="battle-tap"]').disabled, true);
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
  assert.match(env.client.renderResult(result), /Net: \+9 ✦/);
  assert.match(env.client.renderResult(result), /Payout 19/);
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
  battle.opponent.is_bot = false;
  assert.match(env.client.renderBattle(battle), /opponent’s taps can lower it/);
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
