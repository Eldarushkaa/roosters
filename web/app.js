/**
 * Rooster Club Mini App. Zero-build UI over the versioned JSON API.
 *
 * The server owns balances, accepted taps, matchmaking and outcomes. Local time
 * only draws countdowns; it never settles a battle or awards a reward. Every
 * mutation is saved with its UUID before sending, so a lost response can be
 * retried without buying, charging or rewarding twice.
 */
import { createI18n, botNameKeys } from './i18n.mjs';
import { createAppEnvironment } from './environment.mjs';
import { icon, iconText } from './icons.mjs';
import { createFeedback } from './feedback.mjs';
import { createGuide } from './guide.mjs';
import { renderRooster, roosterBattleFrame } from './rooster-art.mjs';
import { createBattleInput } from './battle-input.mjs';

const i18n = createI18n({
  getItem: (key) => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
});
const t = (key, params) => i18n.t(key, params);
const API = '/api/v1';
const STORAGE_PREFIX = 'rooster.v1.';
const $ = (selector) => document.querySelector(selector);
const main = $('#main');
const environment = createAppEnvironment();
const telegram = environment.telegram;
const feedback = createFeedback({ telegram });
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
const tr = (key, params) => escapeHTML(t(key, params));
const rich = (key, params) => iconText(t(key), params, t('shell.coins'));
const coin = () => icon('coin', t('shell.coins'));
const fmt = (value) => i18n.formatNumber(value);
const money = (minor) => i18n.formatMoney(minor);
const signedMoney = (minor) => `${minor > 0 ? '+' : minor < 0 ? '−' : ''}${money(Math.abs(minor || 0))}`;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
const disabled = (condition) => condition ? ' disabled' : '';

/** Storage may be unavailable in embedded/private browsers. Play still works. */
const storage = {
  get(key, persistent = false) { try { return (persistent ? localStorage : sessionStorage).getItem(STORAGE_PREFIX + key); } catch { return null; } },
  set(key, value, persistent = false) { try { (persistent ? localStorage : sessionStorage).setItem(STORAGE_PREFIX + key, value); } catch { /* Session-only fallback. */ } },
  remove(key) { try { sessionStorage.removeItem(STORAGE_PREFIX + key); } catch { /* Nothing to remove. */ } },
};
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (char) => (Number(char) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(char) / 4).toString(16));
}
function readPending() {
  try {
    const saved = JSON.parse(storage.get('pending') || 'null');
    return saved && typeof saved.path === 'string' && typeof saved.key === 'string' && saved.body && typeof saved.owner === 'string' ? saved : null;
  } catch { return null; }
}

let state = null;
let config = null;
let token = storage.get('token');
let tab = ['arena', 'gear', 'roost', 'leaderboard'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'arena';
let busy = false;
let refreshing = false;
let booting = false;
let pending = readPending();
let stateVersion = 0;
let serverOffset = 0;
let lastPoll = 0;
let lastPresence = 0;
let lastLeaderboard = 0;
let noticeTimer;
let commandRetryTimer;
let battleBuffer = 0;
let bufferedBattleId = null;
let leaderboard = null;
let leaderboardLoading = false;
let leaderboardError = '';
let devIdentity = null;
let selectedStake = Number(storage.get('stakeMinor')) || null;
let selectedMode = storage.get('arenaMode') === 'online' ? 'online' : 'bot';
let arenaHelpMode = null;
let stakeQuote = null;
let stakeQuoteTimer = null;
let stakeQuoteSequence = 0;
let stakeQuotePendingKey = null;
let stakeQuoteError = null;
let ranking = 'power';
let hitAnimations = [];
let hitSequence = 0;
let spaceHeld = false;
let spaceHandled = false;
let connectionState = null;
let visibleNotice = null;
let gearFeedback = null;
const roostFeedback = {};
let roostCopying = false;
let bootFailure = null;
const seenResultToasts = new Map();
let resultToast = null;
let resultToastUntil = 0;
let resultToastTimer = null;
let resultToastRenderKey = null;
let resultReturnBattleId = null;

const guide = createGuide({
  overlay: $('#guide-overlay'),
  dialog: $('#quick-guide'),
  trigger: $('#open-guide'),
  background: [$('.app-shell'), $('#navigation'), $('#battle-result-toast'), $('#notice-stack')],
  storage: { getItem: key => localStorage.getItem(key), setItem: (key, value) => localStorage.setItem(key, value) },
  getContent: renderGuideContent,
  getLanguage: () => i18n.getLanguage(),
  fallbackFocus: () => main,
});

function renderGuideContent() {
  const rules = state.catalog.battle;
  const language = i18n.getLanguage();
  const coach = '<img class="guide-coach" src="/static/assets/guide/rooster-coach.webp" width="640" height="534" alt="">';
  const fighter = '<img class="guide-fighter" src="/static/assets/arena/rooster-hero.webp" width="1254" height="1254" alt="">';
  // Illustrations are presentation only: no stake selectors or game commands.
  const art = {
    stake: `<div class="guide-coins">${icon('coin')}${icon('coin')}${icon('coin')}</div><span class="guide-choice">${icon('check')}</span>`,
    training: `${fighter}<span class="guide-versus ui-display">VS</span>${icon('bot')}`,
    taps: `<span class="guide-fight-label">${icon('swords')}${tr('guide.fight')}</span>${icon('power')}`,
    online: `${fighter}<span class="guide-versus ui-display">VS</span><span class="guide-rival">${fighter}</span>`,
    gear: `${coach}<span class="guide-equipment"><span>${icon('swords')}</span><span>${icon('gear')}</span><span>${icon('feather')}</span></span>`,
    rewards: `${coach}<img class="guide-chest" src="/static/assets/guide/reward-chest.webp" width="480" height="499" alt="">`,
  };
  const steps = ['stake', 'training', 'taps', 'online', 'gear', 'rewards'];
  return `<header class="guide-header"><div><h2 id="guide-title" class="ui-title">${tr('guide.title')}</h2><span class="guide-subtitle ui-display">${tr('guide.eyebrow')}</span></div><button type="button" class="ui-button guide-close" data-action="close-guide" data-focus="guide-close" aria-label="${tr('guide.close')}">${icon('close')}</button></header>
    <div class="guide-content" tabindex="0" data-focus="guide-content" role="region" aria-labelledby="guide-title"><div class="guide-intro"><div class="guide-mascot" aria-hidden="true">${coach}</div><p class="guide-speech">${tr('guide.motto')}</p></div><ol class="guide-steps">${steps.map((key, index) => `<li class="guide-card guide-card-${key}"><div class="guide-card-copy"><span class="guide-number ui-display" aria-hidden="true">${index + 1}</span><div><h3 class="ui-title">${tr(`guide.${key}Title`)}</h3><p>${tr(`guide.${key}Body`, { taps: fmt(rules.tap_cap), seconds: fmt(rules.duration) })}</p></div></div><div class="guide-art" aria-hidden="true">${art[key]}</div></li>`).join('')}</ol><p class="guide-details">${tr('guide.details', { taps: fmt(rules.tap_cap), seconds: fmt(rules.duration) })}</p><p class="guide-tip">${icon('power')}<span>${tr('guide.tip')}</span></p></div>
    <footer class="guide-footer"><div class="language-switch ui-segmented-compact" role="group" aria-label="${tr('shell.language')}">${['ru', 'en'].map(value => `<button type="button" class="ui-button" data-language="${value}" data-focus="guide-language-${value}" lang="${value}" aria-pressed="${language === value}">${value.toUpperCase()}</button>`).join('')}</div><button type="button" class="ui-button ui-primary" data-action="close-guide" data-focus="guide-done">${tr('guide.done')}</button></footer>`;
}

function syncGuide() {
  guide.sync({
    available: Boolean(state) && !bootFailure && !activeBattle(),
    autoAllowed: !booting && !state?.queue && !blocked() && !resultToast,
  });
}

function closeResultToast() {
  clearTimeout(resultToastTimer);
  resultToastTimer = null;
  resultToast = null;
  resultToastRenderKey = null;
  const toast = $('#battle-result-toast');
  if (toast?.contains?.(document.activeElement)) {
    const trigger = [...document.querySelectorAll('[data-action="open-result"]')]
      .find(node => node.dataset.battleId === resultReturnBattleId);
    (trigger || main).focus({ preventScroll: true });
  }
  resultReturnBattleId = null;
  if (toast) toast.hidden = true;
}

function acceptResultToast(next) {
  const completed = next.battle?.status === 'finished' && next.battle.result ? next.battle
    : next.history?.find(item => item.status === 'finished' && item.result);
  if (state?.player.id !== next.player.id || next.battle?.status === 'active' || next.queue) closeResultToast();
  if (!completed) return;
  const key = `resultToast.${next.player.id}`;
  if (seenResultToasts.get(next.player.id) === completed.id || storage.get(key) === completed.id) return;
  seenResultToasts.set(next.player.id, completed.id);
  storage.set(key, completed.id);
  // History stays available; only newly observed results get an expiring notice.
  if (next.battle?.status === 'active' || next.queue) return;
  closeResultToast();
  resultToast = completed;
  resultToastUntil = completed.opponent?.is_bot === false ? Infinity : Date.now() + 4000;
  if (completed.opponent?.is_bot !== false) resultToastTimer = setTimeout(() => { closeResultToast(); syncGuide(); }, 4000);
}

function renderTapAnalysis(battle) {
  if (battle.opponent?.is_bot !== false) return '';
  const percent = value => Number.isFinite(value) ? `${fmt(Math.round(value * 10000) / 100)}%` : '—';
  const rows = ['you', 'opponent'].map(side => {
    const fighter = battle[side] || {};
    const stats = battle.tap_analysis?.[side];
    const change = Number.isFinite(stats?.change) ? Math.round(stats.change * 10000) / 100 : null;
    const delta = change === null ? '—' : `${change > 0 ? '+' : change < 0 ? '−' : ''}${fmt(Math.abs(change))}`;
    return `<tr><th scope="row"><span>${tr(side === 'you' ? 'result.you' : 'result.opponent')}</span><strong>${escapeHTML(fighterName(fighter))}</strong></th><td data-label="${tr('result.taps')}">${Number.isFinite(fighter.taps) ? fmt(fighter.taps) : '—'}</td><td data-label="${tr('result.oddsPath')}"><span class="result-chance-path">${percent(stats?.initial_probability)} <span aria-hidden="true">→</span> ${percent(stats?.final_probability)}</span><span class="result-chance-change" data-tone="${change > 0 ? 'success' : change < 0 ? 'error' : 'neutral'}">${tr('result.chanceChange', { delta })}</span></td></tr>`;
  }).join('');
  return `<div class="result-analysis"><h3 class="ui-title">${tr('result.analysisTitle')}</h3><p class="ui-muted">${tr('result.analysisNote')}</p><table role="table"><thead><tr><th scope="col">${tr('result.fighter')}</th><th scope="col">${tr('result.taps')}</th><th scope="col">${tr('result.oddsPath')}</th></tr></thead><tbody>${rows}</tbody></table>${!battle.tap_analysis ? `<p class="ui-muted">${tr('result.analysisUnavailable')}</p>` : ''}</div>`;
}

function renderResultToast() {
  const toast = $('#battle-result-toast');
  if (!toast || !resultToast) return;
  if (Date.now() >= resultToastUntil) { closeResultToast(); return; }
  const renderKey = `${resultToast.id}:${i18n.getLanguage()}`;
  if (resultToastRenderKey === renderKey) return;
  resultToastRenderKey = renderKey;
  const focused = toast.contains?.(document.activeElement);
  const scrollTop = toast.scrollTop;
  const result = resultToast.result;
  const timed = Number.isFinite(resultToastUntil);
  toast.hidden = false;
  toast.classList.add('ui-result');
  toast.classList.toggle('loss', !result.won);
  toast.dataset.tone = result.won ? 'success' : 'error';
  toast.innerHTML = `<button type="button" class="result-toast-close ui-button ui-accent" data-action="close-result" aria-label="${tr('result.close')}">${timed ? '<svg class="result-close-timer" viewBox="0 0 44 44" aria-hidden="true"><circle class="result-close-track" cx="22" cy="22" r="19"/><circle id="result-close-progress" cx="22" cy="22" r="19" pathLength="100"/></svg>' : ''}${icon('close')}</button><div class="result-toast-heading ui-result-heading"><span class="result-toast-mark" aria-hidden="true">${icon(result.won ? 'trophy' : 'feather')}</span><h2 class="ui-outcome">${tr(result.won ? 'common.win' : 'common.loss')}</h2></div><strong class="result-toast-net ui-net" data-tone="${result.net_minor < 0 ? 'error' : 'success'}">${rich('result.net', { amount: signedMoney(result.net_minor) })}</strong><p class="result-toast-details"><span>${tr('result.payout', { amount: money(result.payout_minor) })}</span><span>${tr('result.xp', { amount: fmt(result.xp) })}</span></p>${renderTapAnalysis(resultToast)}<small class="ui-muted">${tr(timed ? 'result.autoClose' : 'result.saved')}</small>`;
  toast.scrollTop = scrollTop || 0;
  if (focused) toast.querySelector('[data-action="close-result"]')?.focus({ preventScroll: true });
  updateResultToastTimer();
}

function updateResultToastTimer() {
  if (!resultToast || !Number.isFinite(resultToastUntil)) return;
  if (Date.now() >= resultToastUntil) { closeResultToast(); syncGuide(); return; }
  const progress = $('#result-close-progress');
  if (progress) progress.style.strokeDashoffset = String(100 * (1 - (resultToastUntil - Date.now()) / 4000));
}

/** One bounded cosmetic hit at a time; it never changes accepted taps or odds. */
function clearBattleHit() {
  for (const animation of hitAnimations) animation.cancel();
  hitAnimations = [];
}

function animateBattleHit() {
  clearBattleHit();
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  const angle = ++hitSequence % 2 ? -28 : 28;
  const play = (element, frames, duration) => {
    // Older embedded browsers still accept taps if Web Animations is absent.
    if (!element?.animate) return;
    const animation = element.animate(frames, { duration, easing: 'ease-out' });
    hitAnimations.push(animation);
  };
  if (reducedMotion) {
    // No movement, particles or repeated bright flashes in reduced-motion mode.
    play($('.hit-slash'), [{ opacity: .35 }, { opacity: 0 }], 180);
    return;
  }
  play($('[data-fighter="you"]'), [
    { transform: 'translate(0, 0) rotate(0)' },
    { transform: `translate(var(--strike-distance), -8px) rotate(${angle / 3}deg) scale(1.08)`, offset: .24 },
    { transform: 'translate(0, 0) rotate(0)' },
  ], 280);
  play($('[data-fighter="opponent"]'), [
    { transform: 'translate(0, 0) rotate(0)' },
    { transform: `translate(12px, -3px) rotate(${angle / 2}deg)`, offset: .28 },
    { transform: 'translate(-3px, 0) rotate(-3deg)', offset: .65 },
    { transform: 'translate(0, 0) rotate(0)' },
  ], 280);
  play($('.hit-slash'), [
    { opacity: .85, transform: `rotate(${angle}deg) scale(.6)` },
    { opacity: .7, transform: `rotate(${angle + 18}deg) scale(1.2)`, offset: .4 },
    { opacity: 0, transform: `rotate(${angle + 28}deg) scale(1.45)` },
  ], 220);
  for (const spark of document.querySelectorAll('.hit-spark')) {
    play(spark, [
      { opacity: .8, transform: 'translate(0, 0) scale(1)' },
      { opacity: 0, transform: 'translate(var(--spark-x), var(--spark-y)) scale(.25)' },
    ], 280);
  }
}

class ApiError extends Error {
  constructor(message, status = 0, code = '', translationKey = null, params = {}) {
    super(message); this.status = status; this.code = code;
    this.translationKey = translationKey; this.params = params;
  }
  get uncertain() { return !this.status || this.status >= 500; }
}

function localizedError(key, status = 0, code = '', params = {}) {
  return new ApiError(t(key, params), status, code, key, params);
}

function errorMessage(error) {
  return { key: error.translationKey || i18n.errorKey(error.code), params: error.params || {} };
}

function messageText(message) {
  if (!message || typeof message !== 'object') return String(message || '');
  const params = Object.fromEntries(Object.entries(message.params || {}).map(([key, value]) =>
    [key, value && typeof value === 'object' && value.key ? messageText(value) : value]));
  return t(message.key, params);
}

async function request(path, { body, key, auth = true, retryAuth = true, timeoutMs = 12000 } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(API + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(auth && token ? { Authorization: `Bearer ${token}` } : {}), ...(key ? { 'Idempotency-Key': key } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
      cache: 'no-store',
    });
    if (response.status === 401 && auth && retryAuth) {
      clearTimeout(timeout);
      try { await authenticate({ applyState: false }); }
      catch (error) {
        // An auth refresh says nothing about a previously sent mutation. Keep
        // its key even if an expired Telegram launch now needs reopening.
        error.keepPending = true;
        throw error;
      }
      return request(path, { body, key, auth, retryAuth: false, timeoutMs });
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw localizedError(i18n.errorKey(payload?.error?.code), response.status, payload?.error?.code, { status: response.status });
    if (!payload) throw localizedError('error.invalid_response');
    setConnection(true);
    return payload;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    setConnection(false);
    throw localizedError(error.name === 'AbortError' ? 'error.timeout' : 'error.offline');
  } finally { clearTimeout(timeout); }
}

function developmentIdentity() {
  if (devIdentity) return devIdentity;
  try { devIdentity = JSON.parse(storage.get('identity', true) || 'null'); } catch { devIdentity = null; }
  if (!devIdentity?.user_id || !devIdentity?.name) {
    const id = uuid();
    devIdentity = { user_id: id, name: t('guest', { id: id.slice(0, 4).toUpperCase() }) };
    storage.set('identity', JSON.stringify(devIdentity), true);
  }
  return devIdentity;
}

/**
 * An unsigned launch ID is only a cache selector, never authentication. The
 * saved token is signed by the server. A different launch account, malformed
 * initData, or a dev/Telegram switch always requires fresh server validation.
 */
function currentAuthContext() {
  if (telegram?.initData) {
    try {
      const user = JSON.parse(new URLSearchParams(telegram.initData).get('user'));
      return Number.isSafeInteger(user?.id) && user.id > 0 ? `tg:${user.id}` : null;
    } catch { return null; }
  }
  return config?.dev_auth ? `dev:${developmentIdentity().user_id}` : null;
}

async function authenticate({ applyState = true } = {}) {
  let response;
  if (telegram?.initData) {
    response = await request('/auth/telegram', { body: { init_data: telegram.initData }, auth: false });
  } else if (config?.dev_auth) {
    response = await request('/auth/dev', { body: developmentIdentity(), auth: false });
  } else {
    throw localizedError('error.telegram_required', 403, 'telegram_required');
  }
  token = response.token;
  storage.set('token', token);
  storage.set('authContext', response.state.player.id);
  // During renewal the original caller owns state ordering. An old auth
  // response must not overwrite a purchase that completed in another request.
  if (applyState) acceptState(response.state);
}

function acceptState(next) {
  if (!next?.player || !next?.catalog) throw localizedError('error.invalid_state');
  const previous = state;
  const previousToast = resultToast?.id;
  acceptResultToast(next);
  state = next;
  serverOffset = Number(next.server_time) * 1000 - Date.now();
  if (!Number.isFinite(serverOffset)) serverOffset = 0;
  if (bufferedBattleId !== state.battle?.id || state.battle?.status !== 'active' || state.battle.you.taps >= state.battle.tap_cap) { battleBuffer = 0; bufferedBattleId = state.battle?.id || null; }
  render();
  feedback.balance(previous?.player, next.player);
  if (resultToast && resultToast.id !== previousToast) {
    feedback.result(resultToast, previous?.player.id === next.player.id
      && previous?.battle?.status === 'active' && previous.battle.id === resultToast.id);
  }
  // A state poll can discover a committed start/join whose answer was lost.
  // Recover its original command before queued taps, without locking input.
  scheduleCombatRetry();
}

/** Only a server-confirmed active fight makes start/join recovery automatic. */
function combatRecoveryCommand(command = pending) {
  if (!command || command.owner !== state?.player.id) return false;
  return command.path === '/battle/tap'
    || (activeBattle() && ['/presence', '/battle/start', '/queue/join'].includes(command.path));
}

function scheduleCombatRetry(command = pending) {
  if (pending !== command || busy || document.hidden || commandRetryTimer
      || command?.recoveryBlocked || !combatRecoveryCommand(command)) return;
  const delay = Math.min(500 * 2 ** Math.min(Math.max(0, (command.attempts || 1) - 1), 2), 2000);
  commandRetryTimer = setTimeout(() => {
    commandRetryTimer = null;
    if (pending === command && !document.hidden && !command.recoveryBlocked && combatRecoveryCommand(command)) return sendPending();
  }, delay);
}

/** Serialize all writes. The saved payload is immutable until an answer arrives. */
async function mutate(path, body = {}, options = {}) {
  if (busy || pending || !state) return false;
  pending = { path, body, key: uuid(), owner: state.player.id, attempts: 0, messageKey: options.messageKey || '', quiet: Boolean(options.quiet) };
  storage.set('pending', JSON.stringify(pending));
  return sendPending();
}

async function sendPending() {
  if (busy || !pending || !state) return false;
  clearTimeout(commandRetryTimer);
  commandRetryTimer = null;
  if (pending.owner !== state.player.id) {
    pending = null;
    storage.remove('pending');
    return false;
  }
  const command = pending;
  command.attempts = (command.attempts || 0) + 1;
  delete command.recoveryBlocked;
  storage.set('pending', JSON.stringify(command));
  if (gearCommandKey(command)) gearFeedback = null;
  const roostKey = roostCommandKey(command);
  if (roostKey) delete roostFeedback[roostKey];
  busy = true;
  stateVersion += 1;
  render();
  try {
    // Recover an initially stuck request quickly, then allow slower healthy
    // replies through instead of aborting every retry at the same deadline.
    // UUID/body remain unchanged, including a start/join discovered by a poll.
    const timeoutMs = combatRecoveryCommand(command)
      ? Math.min(2500 * 2 ** Math.min(command.attempts - 1, 2), 8000) : 12000;
    const response = await request(command.path, { body: command.body, key: command.key, timeoutMs });
    if (!response.state?.player || !response.state?.catalog) throw localizedError('error.incomplete_response');
    pending = null;
    storage.remove('pending');
    busy = false;
    recordGearFeedback(command, { key: command.path === '/gear/upgrade' ? 'gear.upgraded' : 'gear.selected' });
    if (roostKey) recordRoostFeedback(roostKey, { key: roostKey === 'daily' ? 'notice.dailyClaimed' : 'notice.passiveClaimed' }, false, command.owner, response.result?.payout_minor);
    // The saved command result is replayed, accompanied by fresh server state.
    acceptState(response.state);
    feedback.command(command.path, command.key);
    if (command.attempts > 1) lastPoll = 0;
    // Older saved commands contain Russian prose. Derive their known success
    // key from the operation, never show stale-language text after a retry.
    const legacyMessageKeys = { '/claim/daily': 'notice.dailyClaimed', '/claim/passive': 'notice.passiveClaimed', '/gear/upgrade': 'notice.gearUpgraded', '/breed/buy': 'notice.breedChanged' };
    const messageKey = command.messageKey || (command.message ? legacyMessageKeys[command.path] : null);
    if (messageKey) showNotice({ key: messageKey });
    else if (!command.quiet || $('[data-action="retry"]')) clearNotice();
    return true;
  } catch (error) {
    busy = false;
    feedback.command(command.path, command.key, true);
    recordGearFeedback(command, errorMessage(error), true);
    if (roostKey) recordRoostFeedback(roostKey, errorMessage(error), true, command.owner);
    if (!error.uncertain && !error.keepPending) {
      pending = null;
      storage.remove('pending');
      showNotice(errorMessage(error), { error: true });
      await refreshState();
    } else {
      // Authentication failures need explicit recovery. A later state poll or
      // visibility event must not turn them into an automatic retry loop.
      command.recoveryBlocked = Boolean(error.keepPending);
      storage.set('pending', JSON.stringify(command));
      showNotice({ key: 'notice.retrySafe', params: { message: errorMessage(error) } }, { error: true, retry: true });
      // Keep ordinary economic actions on their explicit recovery path. During
      // combat, recover transient transport failures without blocking input.
      if (error.uncertain && !error.keepPending) scheduleCombatRetry(command);
    }
    render();
    return false;
  }
}

async function refreshState() {
  if (!token || busy || refreshing || document.hidden) return;
  refreshing = true;
  const generation = stateVersion;
  try {
    const next = await request('/state');
    if (generation === stateVersion && !busy) acceptState(next);
  } catch (error) { setConnection(false); }
  finally { refreshing = false; lastPoll = Date.now(); }
}

function setConnection(connected) {
  connectionState = connected;
  $('#connection').classList.toggle('offline', !connected);
  const label = t(connected ? 'shell.connected' : 'shell.offline');
  $('#connection-label').textContent = label;
  $('#connection').setAttribute('aria-label', label);
  $('#connection').setAttribute('title', label);
}

function showNotice(message, options = {}) {
  clearTimeout(noticeTimer);
  visibleNotice = { message, options };
  renderNotice();
  if (!options.retry) noticeTimer = setTimeout(clearNotice, options.error ? 9000 : 4500);
}
function renderNotice() {
  if (!visibleNotice) return;
  const { message, options } = visibleNotice;
  $('#notice-stack').innerHTML = `<div class="notice ui-status${options.error ? ' error' : ''}" data-tone="${options.error ? 'error' : 'success'}"><span>${escapeHTML(messageText(message))}</span>${options.retry ? `<button type="button" class="ui-button" data-action="retry">${tr('common.retry')}</button>` : `<button type="button" class="dismiss ui-button" data-action="dismiss-notice" aria-label="${tr('common.dismissNotice')}">${icon('close')}</button>`}</div>`;
}
function clearNotice() { clearTimeout(noticeTimer); visibleNotice = null; $('#notice-stack').replaceChildren(); }

/** Translate explicitly keyed UI nodes; user/server text is never searched. */
function updateStaticLanguage() {
  if (document.documentElement) document.documentElement.lang = i18n.getLanguage();
  document.title = t('shell.title');
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
  for (const [selector, dataKey, attribute] of [
    ['[data-i18n-aria]', 'i18nAria', 'aria-label'],
    ['[data-i18n-title]', 'i18nTitle', 'title'],
    ['[data-i18n-content]', 'i18nContent', 'content'],
  ]) {
    for (const node of document.querySelectorAll(selector)) node.setAttribute(attribute, t(node.dataset[dataKey]));
  }
  for (const button of document.querySelectorAll('[data-language]')) button.setAttribute('aria-pressed', String(button.dataset.language === i18n.getLanguage()));
  if (connectionState === null) {
    $('#connection-label').textContent = t('shell.connecting');
    $('#connection').setAttribute('aria-label', t('shell.connecting'));
  } else setConnection(connectionState);
}

function changeLanguage(language) {
  if (!['ru', 'en'].includes(language)) return;
  i18n.setLanguage(language);
  updateStaticLanguage();
  if (bootFailure) renderBootFailure();
  else if (state) render();
  renderNotice();
}

function renderBootFailure() {
  if (!bootFailure) return;
  main.innerHTML = `<section class="error-view ui-panel"><span class="loading-rooster" aria-hidden="true">${icon(bootFailure ? 'alert' : 'rooster')}</span><h1 class="ui-headline">${tr(bootFailure.code === 'telegram_required' ? 'boot.telegramTitle' : 'boot.unavailableTitle')}</h1><p class="ui-status" data-tone="error">${escapeHTML(messageText(errorMessage(bootFailure)))}</p><button type="button" class="ui-button ui-primary" data-action="boot-retry">${rich('boot.retry')}</button></section>`;
}
function now() { return (Date.now() + serverOffset) / 1000; }
function blocked() { return busy || Boolean(pending); }
function activeBattle() { return state?.battle?.status === 'active'; }
function battleInputBlocked() {
  if (!pending || busy) return false;
  return pending.path !== '/presence'
    && !(activeBattle() && pending.owner === state.player.id && ['/battle/start', '/queue/join'].includes(pending.path))
    && !(pending.path === '/battle/tap' && pending.body.battle_id === state?.battle?.id);
}
function lockedGear() { return activeBattle() || Boolean(state?.queue); }
function breedName(id) { return i18n.has(`breed.${id}.name`) ? t(`breed.${id}.name`) : state?.catalog.breeds.find((item) => item.id === id)?.name || t('common.rooster'); }
function modeName(mode) { return t(i18n.has(`mode.${mode}`) ? `mode.${mode}` : 'mode.online'); }
function fighterName(item) { return item.is_bot && botNameKeys[item.name] ? t(botNameKeys[item.name]) : item.name; }

/** Original vector artwork; no remote fonts, icon packages or image requests. */
function roosterSVG(id = 'hero', color = '#f5bc69') {
  const safeColor = /^#[0-9a-f]{3,8}$/i.test(color) ? color : '#f5bc69';
  return `<svg class="rooster-svg" viewBox="0 0 330 310" role="img" aria-label="${tr('a11y.rooster')}"><defs><linearGradient id="body-${id}" x1="0" x2="1" y1="0" y2="1"><stop stop-color="${safeColor}"/><stop offset="1" stop-color="#c87d3b"/></linearGradient><linearGradient id="tail-${id}" x1="0" x2="1" y1="0" y2="1"><stop stop-color="#638887"/><stop offset="1" stop-color="#304b53"/></linearGradient><linearGradient id="metal-${id}" x1="0" x2="1"><stop stop-color="#e2ebdc"/><stop offset=".5" stop-color="#92a8a4"/><stop offset="1" stop-color="#ccd9ca"/></linearGradient></defs><ellipse cx="165" cy="276" rx="95" ry="12" fill="#121e25" opacity=".25"/><path d="M144 180C83 202 54 162 51 106c28 8 41 27 52 46C57 100 82 51 108 34c9 36 12 67 12 94C108 84 139 52 161 45c0 31-9 58-23 88C162 104 186 105 203 119c-17 27-38 45-59 61Z" fill="url(#tail-${id})" stroke="#74988b" stroke-width="2"/><path d="M109 43c-6 61 5 93 26 133M60 117c7 35 31 57 65 63M155 56c-2 41-17 69-28 92" fill="none" stroke="#a4be9960" stroke-width="3" stroke-linecap="round"/><path d="m156 220-6 41-15 8m16-8 13 7m39-48 9 40 18 6m-18-6-10 10" fill="none" stroke="#d78d3b" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/><path d="M213 114c-20 8-38 17-61 18-29 1-44 24-42 51 3 34 30 56 65 54 50-3 74-38 67-76Z" fill="url(#body-${id})"/><path d="M178 145c-28-12-50 6-46 33 4 29 29 36 55 17-11-4-8-14-2-20-15 0-20-13-7-30Z" fill="#d58b45" stroke="#f2b864" stroke-width="2"/><path d="M206 130c-24-10-36-39-20-60 10-13 38-11 49 4 10 15 12 38 5 52l-15-5-2 20-10-9-10 11Z" fill="#f5d695"/><path d="m186 76-3-24 13 5 1-23 17 19 12-16 5 23 17-4-9 26" fill="#f27359" stroke="#f88d64" stroke-width="2" stroke-linejoin="round"/><path d="m240 94 32 13-31 10Z" fill="#f4b54e"/><path d="m244 107 20 0" stroke="#c58035" stroke-width="2"/><path d="M229 120c18 6 18 23 7 29-10 5-16-6-14-14Z" fill="#e96b50"/><circle cx="228" cy="92" r="9" fill="#fff2bf"/><circle cx="230" cy="93" r="4.5" fill="#203038"/><circle cx="231.5" cy="91.3" r="1.5" fill="#fff"/><path d="m214 84 23-4" stroke="#715137" stroke-width="5" stroke-linecap="round"/><path d="m178 132 16 15-12 31-11-6 8-24-10-12Z" fill="#334750"/><path d="M183 146c13 14 27 24 48 31l-12 28c-28-7-44-20-60-40Z" fill="#4b6570" stroke="#8ca39c" stroke-width="3"/><path d="m207 159-8 27m-4-35-10 25" stroke="#a6b6a6" stroke-width="2"/><path d="m232 162 8-34 38-77 10-8 2 15-37 79-17 29Z" fill="url(#metal-${id})" stroke="#dce5d5" stroke-width="2"/><path d="m277 66-34 72" stroke="#f9f6dc" stroke-width="2"/><path d="m228 132 27 12" stroke="#e9b65c" stroke-width="7" stroke-linecap="round"/><path d="m239 144-9 19" stroke="#886149" stroke-width="7"/><circle cx="228" cy="165" r="10" fill="#efb267"/><path d="M124 166c-11 8-23 10-35 10l1 43c1 18 22 30 34 36 13-6 33-19 33-37v-42c-14 0-23-3-33-10Z" fill="#304d54" stroke="#d4b373" stroke-width="5"/><path d="m124 180-20 33 20 22 20-22Z" fill="#e5bb6c"/><path d="m124 188-12 23 12 15 12-15Z" fill="#324c4a"/><circle cx="101" cy="186" r="3" fill="#e5bb6c"/><circle cx="146" cy="186" r="3" fill="#e5bb6c"/></svg>`;
}

function slotSVG(slot) {
  const shapes = {
    helmet: '<path d="M12 40v-9C12 18 20 10 32 10s20 8 20 21v9H38v12H26V40Z" fill="var(--ui-muted)" stroke="var(--ui-ink)" stroke-width="3"/><path d="M31 10v24M17 31h11m8 0h11" stroke="var(--ui-ink)" stroke-width="5"/><path d="m25 8 7-7 7 7" fill="var(--ui-gold)" stroke="var(--ui-ink)" stroke-width="2"/>',
    armor: '<path d="m20 11 12 7 12-7 13 14-10 10-5-5v23H22V30l-5 5L7 25Z" fill="var(--ui-muted)" stroke="var(--ui-ink)" stroke-width="3"/><path d="m24 20 8 4 8-4v20l-8 7-8-7Z" fill="var(--ui-panel-raised)"/><path d="M32 24v18" stroke="var(--ui-gold)" stroke-width="3"/>',
    sword: '<path d="m20 43 9-16L47 7l9-3-2 10-20 22-11 10Z" fill="var(--ui-muted)" stroke="var(--ui-ink)" stroke-width="3"/><path d="m14 35 17 16" stroke="var(--ui-gold)" stroke-width="6" stroke-linecap="round"/><path d="m21 44-9 11" stroke="var(--ui-gold)" stroke-width="8" stroke-linecap="round"/><path d="m30 29 19-19" stroke="var(--ui-text)" stroke-width="2"/>',
  };
  return `<svg viewBox="0 0 64 64" aria-hidden="true">${shapes[slot] || shapes.sword}</svg>`;
}

function render() {
  if (!state) return;
  // An active server battle owns the screen, including reloads from another tab.
  const battling = activeBattle();
  if (battling && tab !== 'arena') {
    tab = 'arena';
    try { history.replaceState(null, '', '#arena'); } catch { /* Embedded origin. */ }
  }
  const overviewScroll = $('.battle-overview')?.scrollTop || 0;
  if (battling && !$('.app-shell').classList.contains('battle-mode')) window.scrollTo({ top: 0, behavior: 'instant' });
  $('.app-shell').classList.toggle('battle-mode', battling);
  const arenaHome = tab === 'arena' && !battling && !state.queue;
  $('.app-shell').classList.toggle('arena-home', arenaHome);
  $('.app-shell').classList.toggle('arena-returning', arenaHome && !state.economy.first_free_battle_available);
  // All live screens now share the same visual environment, including fallback states.
  $('.app-shell').classList.add('ui-shell');
  document.documentElement.classList.add('ui-game-screen');
  const focused = document.activeElement?.dataset.focus;
  // Keep the current fighter nodes through pending-command and polling renders.
  // Their snapshots are frozen for the battle; updateTimers refreshes the reveal.
  // Replacing these nodes every 400 ms would cut a click's animation short.
  const fighters = tab === 'arena' && activeBattle() ? $('.fighters') : null;
  const keepFighters = fighters?.dataset.battleId === state.battle?.id ? fighters : null;
  if (!keepFighters) clearBattleHit();
  const player = state.player;
  $('#player-bar').hidden = false;
  $('#navigation').hidden = battling;
  $('#dev-banner').hidden = !player.is_dev;
  $('#player-name').textContent = player.name;
  $('#avatar').textContent = [...player.name].slice(0, 1).join('').toUpperCase() || t('shell.avatarFallback');
  $('#balance').textContent = money(player.balance_minor);
  document.querySelectorAll('[data-tab]').forEach((button) => { const selected = button.dataset.tab === tab; button.classList.toggle('active', selected); selected ? button.setAttribute('aria-current', 'page') : button.removeAttribute('aria-current'); });
  const ongoing = tab !== 'arena' && (activeBattle() || state.queue) ? `<div class="context-banner ui-status"><span>${rich(activeBattle() ? 'context.battle' : 'context.queue')}</span><button class="ui-button" type="button" data-action="go-arena">${rich('context.arena')}</button></div>` : '';
  if (arenaHome) ensureStakeQuote();
  renderKeepingPrimaryControl(ongoing + ({ arena: renderArena, gear: renderGear, roost: renderRoost, leaderboard: renderLeaderboard })[tab](), Boolean(keepFighters));
  if (keepFighters) $('.fighters').replaceWith(keepFighters);
  localizeFighters();
  if (focused) [...main.querySelectorAll('[data-focus]')].find((element) => element.dataset.focus === focused)?.focus({ preventScroll: true });
  renderResultToast();
  updateTimers();
  // The caption and restored fighters affect the scroll range. Restore only
  // after their final layout, so a poll cannot clamp a user's scroll to zero.
  if ($('.battle-overview')) $('.battle-overview').scrollTop = overviewScroll;
  syncGuide();
}

/** Keep the active input and its ancestors connected during state updates.
 * Removing/reinserting even the same button cancels a phone's unfinished tap.
 * Preserve its text children too: the touch target may be the nested label.
 * The same path preservation protects the Arena stake slider while dragging.
 */
function renderKeepingPrimaryControl(markup, keepBattle = false) {
  const selector = keepBattle ? '[data-action="battle-tap"]' : '#arena-stake-range';
  const current = $(selector);
  if (!current) { main.innerHTML = markup; return; }
  const template = document.createElement('template');
  template.innerHTML = markup;
  const next = template.content.querySelector(selector);
  if (!next) { main.innerHTML = markup; return; }
  const path = (node, root) => {
    const nodes = [];
    for (; node !== root; node = node.parentNode) nodes.unshift(node);
    return nodes;
  };
  const before = path(current, main), after = path(next, template.content);
  if (before.length !== after.length || before.some((node, i) => node.tagName !== after[i].tagName)) {
    main.innerHTML = markup;
    return;
  }
  let parent = main, replacement = template.content;
  for (let i = 0; i < before.length; i += 1) {
    const kept = before[i], fresh = after[i];
    for (const child of [...parent.childNodes]) if (child !== kept) child.remove();
    let passed = false;
    for (const child of [...replacement.childNodes]) {
      if (child === fresh) { passed = true; continue; }
      parent.insertBefore(child, passed ? null : kept);
    }
    for (const attribute of [...kept.attributes]) if (!fresh.hasAttribute(attribute.name)) kept.removeAttribute(attribute.name);
    for (const attribute of fresh.attributes) {
      if (kept.getAttribute(attribute.name) !== attribute.value) kept.setAttribute(attribute.name, attribute.value);
    }
    parent = kept;
    replacement = fresh;
  }
  if (keepBattle) {
    // updateTimers owns the title and its phase/language cache. Do not overwrite
    // it with the template's placeholder while retaining that cache attribute.
    const hint = current.querySelector('span'), nextHint = next.querySelector('span');
    if (hint.textContent !== nextHint.textContent) hint.textContent = nextHint.textContent;
  } else if (current.value !== next.value) current.value = next.value;
}

function localizeFighters() {
  if (tab !== 'arena' || !activeBattle()) return;
  for (const [side, item] of [['you', state.battle.you], ['opponent', state.battle.opponent]]) {
    const name = $(`[data-fighter-name="${side}"]`);
    const role = $(`[data-fighter-role="${side}"]`);
    const power = $(`[data-power-value="${side}"]`);
    if (name) name.textContent = fighterName(item);
    if (role) role.textContent = t(side === 'you' ? 'battle.yourFighter' : item.is_bot ? 'battle.bot' : 'battle.player');
    if (power) power.textContent = side === 'opponent' && now() < state.battle.starts_at && state.battle.roulette ? '—' : fmt(item.power);
  }
  for (const art of document.querySelectorAll('.fighters .rooster-art[role="img"]')) art.setAttribute('aria-label', t('a11y.rooster'));
}

function stakeLimits() {
  return state.economy.stake_limits;
}

function stakeSelectionBlocked() {
  // Presence sends no wager; its background heartbeat must not cancel a drag.
  return blocked() && pending?.path !== '/presence';
}

function currentStake() {
  const { min_minor: min, max_minor: max, step_minor: step } = stakeLimits();
  const previous = selectedStake;
  selectedStake = Number.isSafeInteger(selectedStake) ? clamp(selectedStake, min, Math.max(min, max)) : min;
  selectedStake = min + Math.floor((selectedStake - min) / step) * step;
  if (selectedStake !== previous) storage.set('stakeMinor', String(selectedStake));
  return selectedStake;
}

function stakeQuoteKey() {
  return `${state.player.id}:${state.rules_version}:${state.player.power}:${currentStake()}`;
}

function currentStakeQuotes() {
  const stake = currentStake();
  const cached = stakeQuote?.key === stakeQuoteKey() ? stakeQuote : null;
  return {
    bot: state.economy.bot_quotes?.find(item => item.stake_minor === stake) || cached?.bot,
    online: state.economy.online_quotes?.find(item => item.stake_minor === stake) || cached?.online,
  };
}

function selectStake(value) {
  const { min_minor: min, max_minor: max, step_minor: step } = stakeLimits();
  if (stakeSelectionBlocked() || !Number.isSafeInteger(value) || value < min || value > max || (value - min) % step) return;
  selectedStake = value;
  storage.set('stakeMinor', String(value));
  ensureStakeQuote();
  render();
}

function ensureStakeQuote() {
  if (blocked()) return;
  const quotes = currentStakeQuotes();
  const key = stakeQuoteKey();
  if (currentStake() > stakeLimits().max_minor || (quotes.bot && quotes.online)) {
    clearTimeout(stakeQuoteTimer);
    stakeQuotePendingKey = null;
    stakeQuoteError = null;
    stakeQuoteSequence += 1;
    return;
  }
  if (stakeQuotePendingKey === key || stakeQuoteError?.key === key) return;
  clearTimeout(stakeQuoteTimer);
  stakeQuoteSequence += 1;
  stakeQuotePendingKey = key;
  stakeQuoteError = null;
  stakeQuoteTimer = setTimeout(loadStakeQuote, 150);
}

async function loadStakeQuote() {
  clearTimeout(stakeQuoteTimer);
  if (!state || activeBattle() || state.queue || tab !== 'arena' || blocked()) { stakeQuotePendingKey = null; return; }
  const key = stakeQuoteKey(), stake = currentStake();
  const quotes = currentStakeQuotes();
  if (quotes.bot && quotes.online) return;
  if (stake > stakeLimits().max_minor) return;
  const sequence = ++stakeQuoteSequence;
  stakeQuotePendingKey = key;
  stakeQuoteError = null;
  render();
  try {
    const quote = await request(`/battle/quote?stake_minor=${stake}`);
    if (sequence !== stakeQuoteSequence || key !== stakeQuoteKey()) return;
    const amounts = [quote.bot?.min_payout_minor, quote.bot?.max_payout_minor, quote.online?.win_payout_minor];
    if (quote.stake_minor !== stake || quote.power !== state.player.power || quote.rules_version !== state.rules_version
      || amounts.some(amount => !Number.isSafeInteger(amount) || amount < 0)
      || quote.bot.min_payout_minor > quote.bot.max_payout_minor) throw localizedError('error.invalid_state');
    stakeQuote = { ...quote, key };
  } catch (error) {
    if (sequence === stakeQuoteSequence && key === stakeQuoteKey()) stakeQuoteError = { key, message: errorMessage(error) };
  } finally {
    if (sequence === stakeQuoteSequence) {
      stakeQuotePendingKey = null;
      if (tab === 'arena') render();
    }
  }
}

function renderStakeSelector() {
  const stake = currentStake();
  const { min_minor: min, max_minor: max, step_minor: step } = stakeLimits();
  const quotes = currentStakeQuotes();
  const waiting = stake <= max && !(quotes.bot && quotes.online);
  const failed = stakeQuoteError?.key === stakeQuoteKey();
  const progress = max > min ? (stake - min) / (max - min) * 100 : 0;
  return `<div class="stake-heading"><h3 class="ui-title ui-section-title">${tr('arena.stakeTitle')}</h3><span>${tr('arena.gameCoins')}</span></div><div class="arena-stakes ui-chip-group" role="group" aria-label="${tr('a11y.stake')}">${state.catalog.battle.stakes_minor.map((value) => `<button type="button" class="ui-button ui-chip" data-action="stake" data-stake-minor="${value}" data-focus="stake-${value}" aria-pressed="${value === stake}"${disabled(stakeSelectionBlocked() || value > max || value < min)}><span class="ui-selection">${icon('check')}</span>${money(value)} ${coin()}</button>`).join('')}</div>
    <div class="arena-stake-custom"><div class="arena-stake-amount"><label for="arena-stake-range">${tr('arena.customStake')}</label><output id="arena-stake-value" for="arena-stake-range">${money(stake)} ${coin()}</output></div><input id="arena-stake-range" class="arena-stake-range" type="range" data-action="stake-range" data-focus="stake-range" min="${min}" max="${Math.max(min, max)}" step="${step}" value="${stake}" style="--stake-progress:${progress}%" aria-valuetext="${tr('arena.stakeValue', { amount: money(stake) })}" aria-describedby="arena-stake-bounds"${disabled(stakeSelectionBlocked() || max <= min)}><div id="arena-stake-bounds" class="arena-stake-bounds"><span>${tr('arena.stakeMinimum', { amount: money(min) })}</span><span>${tr('arena.stakeMaximum', { amount: money(max) })}</span></div></div>
    <div class="arena-quote-status ui-status" role="status"${waiting ? '' : ' hidden'}>${tr(failed ? 'arena.quoteFailed' : 'arena.quoteLoading')}${failed ? `<button type="button" class="ui-button" data-action="retry-stake-quote" data-focus="retry-stake-quote">${tr('common.retry')}</button>` : ''}</div>`;
}

function renderModeHelpButton(mode) {
  return `<button type="button" class="ui-button arena-help-toggle" data-action="mode-help" data-mode="${mode}" data-focus="help-${mode}" aria-label="${tr(mode === 'bot' ? 'arena.botHelp' : 'arena.onlineHelp')}" aria-controls="arena-mode-help" aria-expanded="${arenaHelpMode === mode}"><span aria-hidden="true">?</span></button>`;
}

/** Informational only: quotes come from the server, opening help never selects a mode. */
function renderModeHelp() {
  if (!arenaHelpMode) return '<section id="arena-mode-help" hidden></section>';
  const rules = state.catalog.battle;
  const stake = currentStake();
  const bot = arenaHelpMode === 'bot';
  const quote = currentStakeQuotes()[bot ? 'bot' : 'online'];
  const payout = quote ? (bot ? `${money(quote.min_payout_minor)}–${money(quote.max_payout_minor)}` : money(quote.win_payout_minor)) : '—';
  return `<section id="arena-mode-help" class="arena-mode-help ui-panel ui-panel-inset" aria-labelledby="arena-help-title">
    <div class="arena-help-heading"><h3 id="arena-help-title" class="ui-title">${tr(bot ? 'arena.botTitle' : 'arena.onlineTitle')}</h3><button type="button" class="ui-button" data-action="close-mode-help" data-focus="close-mode-help" aria-label="${tr('arena.closeHelp')}">${icon('close')}</button></div>
    <div class="arena-risk" aria-live="polite"><div><span>${tr('arena.lossLabel')}</span><strong>−${money(stake)} ${coin()}</strong></div><div><span>${tr(bot ? 'arena.winPayoutLabel' : 'arena.equalOddsPayout')}</span><strong>${payout} ${coin()}</strong></div></div>
    <p>${tr(bot ? 'arena.botQuoteNote' : 'arena.onlineQuoteNote')}</p>
    <p>${tr(bot ? 'arena.botBody' : 'arena.onlineBody', { min: fmt(Math.round(rules.bot_power_min_ratio * 100)), max: fmt(Math.round(rules.bot_power_max_ratio * 100)) })}</p>
    ${!bot ? `<p>${tr('arena.onlineDisclosure', { multiplier: fmt(rules.pvp_win_multiplier), searching: fmt(state.presence.searching) })}</p>` : ''}
    <p>${tr('arena.helpTaps', { seconds: fmt(rules.duration), taps: fmt(rules.tap_cap) })}</p>
    <p>${tr(bot ? 'arena.helpBot' : 'arena.helpOnline', { min: fmt(Math.round(rules.bot_rtp_min * 100)), max: fmt(Math.round(rules.bot_rtp_max * 100)), taps: fmt(rules.tap_cap), rtp: fmt(Math.round(rules.pvp_rtp * 100)), factor: fmt(rules.pvp_rtp) })}</p>
  </section>`;
}

function closeModeHelp() {
  const mode = arenaHelpMode;
  arenaHelpMode = null;
  render();
  $(`[data-focus="help-${mode}"]`)?.focus({ preventScroll: true });
}

/** Approved Arena art surrounds live player data; it never supplies game values. */
function renderArenaScene(player, onboarding = false) {
  return `<section class="${onboarding ? 'hero ' : ''}arena-scene arena-fighter" aria-label="${tr('shell.yourFighter')}">
    <img class="arena-scene-background" src="/static/assets/arena/arena-background.webp" alt="" aria-hidden="true" width="1536" height="1024" fetchpriority="high">
    ${renderRooster(player, { label: t('a11y.rooster'), className: 'arena-scene-hero', priority: true })}
    <div class="arena-fighter-identity">
      <h1 class="arena-nameplate ui-title">${escapeHTML(breedName(player.breed_id))}</h1>
      <div class="arena-fighter-stats ui-panel ui-panel-inset"><p><span>${tr('common.level', { level: fmt(player.level) })}</span><span>${tr('arena.fighterPower', { power: fmt(player.power) })}</span></p>
        <div class="arena-xp"><div class="ui-progress" role="progressbar" aria-label="${tr('a11y.xp')}" aria-valuemin="0" aria-valuenow="${Number(player.xp_in_level)}" aria-valuemax="${Number(player.xp_to_next)}"><div class="ui-progress-fill" style="width:${clamp(player.xp_in_level / Math.max(1, player.xp_to_next) * 100, 0, 100)}%"></div></div><span>${fmt(player.xp_in_level)} / ${fmt(player.xp_to_next)} XP</span></div>
      </div>
    </div>
  </section>`;
}

function renderArena() {
  const player = state.player;
  const battle = state.battle;
  const rules = state.catalog.battle;
  if (activeBattle()) { arenaHelpMode = null; return renderBattle(battle); }
  if (state.queue) { arenaHelpMode = null; return renderQueue(); }
  const free = state.economy.first_free_battle_available;
  const stake = currentStake();
  const { bot: quote, online: onlineQuote } = currentStakeQuotes();
  const cannotPay = stakeLimits().max_minor < stake;
  if (!free) return renderReturningArena({ player, stake, quote, onlineQuote, cannotPay });
  const firstFight = free ? `<section class="first-fight ui-panel ui-panel-raised"><span class="eyebrow">${tr('arena.firstEyebrow')}</span><h2 class="ui-title">${tr('arena.firstTitle')}</h2><p>${tr('arena.firstBody', { taps: fmt(rules.tap_cap), seconds: fmt(rules.duration), reward: money(rules.free_reward_minor) })}</p><button type="button" class="ui-button ui-primary arena-full" data-action="start-free" data-focus="start-free" aria-busy="${busy && pending?.path === '/battle/start'}"${disabled(blocked())}>${busy && pending?.path === '/battle/start' ? '<span class="ui-spinner" aria-hidden="true"></span>' : ''}${tr(blocked() ? 'arena.processing' : 'arena.firstAction')} ${icon('arrow-up-right')}</button><small>${tr('arena.firstFootnote')}</small></section>` : '';

  return `${firstFight}${renderArenaScene(player, true)}
    <div class="section-title arena-section-title"><h2 class="ui-title ui-section-title">${tr(free ? 'arena.onlineAlternative' : 'arena.readyTitle')}</h2>${presenceHTML()}</div>${renderStakeSelector()}
    <div class="arena-grid single-mode"><article class="mode-card online ui-panel">${renderModeHelpButton('online')}<div class="mode-icon" aria-hidden="true">${icon('arena')}</div><h3 class="ui-title">${tr('arena.onlineTitle')}</h3>${renderModeHelp()}<button class="ui-button ui-accent arena-full" type="button" data-action="queue-join" data-focus="queue-join" aria-busy="${busy && pending?.path === '/queue/join'}"${disabled(blocked() || cannotPay || !onlineQuote)}>${busy && pending?.path === '/queue/join' ? '<span class="ui-spinner" aria-hidden="true"></span>' : ''}${rich(blocked() ? 'arena.processing' : 'arena.onlineAction', { stake: money(stake) })} ${icon('arrow-up-right')}</button></article></div>
    ${cannotPay ? `<p class="muted-note ui-status" role="status">${tr('arena.insufficientFunds')}</p>` : ''}
    ${player.is_dev ? `<p class="muted-note">${tr('arena.devPresence', { count: fmt(state.presence.development_online) })}</p>` : ''}
    ${renderHistory()}`;
}

function renderReturningArena({ player, stake, quote, onlineQuote, cannotPay }) {
  const bot = selectedMode === 'bot';
  const unavailable = !(bot ? quote : onlineQuote);
  const action = bot ? 'start-bot' : 'queue-join';
  return `<div class="arena-play">
    ${renderArenaScene(player)}
    <section class="arena-setup" aria-label="${tr('arena.readyTitle')}">
      <h2 class="ui-title ui-section-title" id="arena-mode-label">${tr('arena.modeTitle')}</h2>
      <div class="arena-modes ui-segmented" role="group" aria-labelledby="arena-mode-label">${['bot', 'online'].map((mode) => `<div class="arena-mode-choice"><button type="button" class="ui-button arena-mode-button${mode === 'online' ? ' ui-accent' : ''}" data-action="arena-mode" data-mode="${mode}" data-focus="mode-${mode}" aria-pressed="${selectedMode === mode}"${disabled(blocked())}>${icon(mode === 'bot' ? 'bot' : 'arena')}<span class="ui-selection">${icon('check')}</span><span class="arena-mode-title">${tr(mode === 'bot' ? 'arena.botTitle' : 'arena.onlineTitle')}</span></button>${renderModeHelpButton(mode)}</div>`).join('')}</div>
      ${renderStakeSelector()}
      ${renderModeHelp()}
      ${cannotPay ? `<p class="arena-unavailable ui-status" role="status">${tr('arena.insufficientFunds')}</p>` : ''}
      <button class="ui-button ui-primary primary arena-full arena-fight" type="button" data-action="${action}" data-focus="${action}" aria-busy="${busy && (pending?.path === '/battle/start' || pending?.path === '/queue/join')}"${disabled(blocked() || cannotPay || unavailable)}>${busy && (pending?.path === '/battle/start' || pending?.path === '/queue/join') ? '<span class="ui-spinner" aria-hidden="true"></span>' : ''}${rich(blocked() ? 'arena.processing' : bot ? 'arena.botAction' : 'arena.onlineFightAction', { stake: money(stake) })}${icon('arrow-up-right')}</button>
    </section>
    <div class="arena-status">${presenceHTML()}${player.is_dev ? `<p>${tr('arena.devPresence', { count: fmt(state.presence.development_online) })}</p>` : ''}</div>
    ${renderHistory()}
  </div>`;
}

function presenceHTML() {
  return `<div class="presence arena-presence"><span class="status-dot" aria-hidden="true"></span><span>${tr('arena.presence', { count: fmt(state.presence.online) })}</span></div>`;
}

function renderQueue() {
  return `<div class="page-heading arena-queue-heading"><span class="eyebrow">${tr('queue.eyebrow')}</span><h1 class="ui-headline">${tr('queue.title')}</h1></div><section class="queue-card ui-panel ui-panel-raised"><div class="radar ui-panel ui-panel-inset" aria-hidden="true">${icon('arena')}</div><h2 class="ui-title">${tr('queue.searching')}</h2><p>${tr('queue.body', { stake: money(state.queue.stake_minor) })}</p>${presenceHTML()}<div class="queue-time ui-badge" id="queue-time"></div><p>${state.player.is_dev ? tr('queue.devBody', { count: fmt(state.presence.development_online) }) : tr('queue.stay')}</p><button type="button" class="ui-button" data-action="queue-leave" data-focus="queue-leave" aria-busy="${busy && pending?.path === '/queue/leave'}"${disabled(blocked())}>${busy && pending?.path === '/queue/leave' ? '<span class="ui-spinner" aria-hidden="true"></span>' : ''}${tr('queue.leave')}</button></section>`;
}

function renderBattle(battle) {
  const preparing = now() < battle.starts_at;
  const roulette = battle.mode === 'bot' && battle.roulette;
  const frame = roosterBattleFrame([battle.you, battle.opponent]);
  const fighter = (item, mine) => {
    const side = mine ? 'you' : 'opponent';
    return `<div><div class="fighter-avatar${mine ? '' : ' opponent'}"><div class="fighter-motion" data-fighter="${side}">${renderRooster(item, { label: t('a11y.rooster'), frame })}</div></div><strong class="fighter-name" data-fighter-name="${side}">${escapeHTML(fighterName(item))}</strong><div class="fighter-meta"><span data-fighter-role="${side}">${tr(mine ? 'battle.yourFighter' : item.is_bot ? 'battle.bot' : 'battle.player')}</span> · <b data-fighter-power="${side}"${mine ? '' : ' id="opponent-power"'}>${icon('power')}<span data-power-value="${side}">${!mine && preparing && roulette ? '—' : fmt(item.power)}</span></b></div></div>`;
  };
  const odds = `<div class="battle-odds" id="battle-odds"${preparing || !Number.isFinite(battle.current_win_probability) ? ' hidden' : ''}><div><span>${tr('battle.winChance')}</span><strong class="ui-title">${Number.isFinite(battle.current_win_probability) ? fmt(Math.round(battle.current_win_probability * 10000) / 100) + '%' : '—'}</strong></div></div>`;
  const hitEffects = '<div class="battle-hit-effects" aria-hidden="true"><span class="hit-slash"></span><span class="hit-spark"></span><span class="hit-spark"></span><span class="hit-spark"></span><span class="hit-spark"></span></div>';
  const maxed = battle.you.taps >= battle.tap_cap;
  const free = battle.wager?.stake_minor === 0;
  const legacy = ['v1', 'v2'].includes(battle.rules_version);
  const duration = Math.max(0.1, battle.starts_at - (battle.created_at ?? battle.starts_at - 2));
  const elapsed = clamp(now() - (battle.created_at ?? battle.starts_at - duration), 0, duration);
  const reel = roulette ? `<div class="roulette ui-panel ui-panel-inset" id="roulette"${preparing ? '' : ' hidden'}><span class="eyebrow">${tr('battle.botPower', { min: fmt(roulette.min_power), max: fmt(roulette.max_power) })}</span><div class="roulette-window ui-badge" data-tone="accent" aria-hidden="true"><div class="roulette-track" style="animation-duration:${duration}s;animation-delay:-${elapsed}s">${[...[0, .25, .5, .75, 1, .25].map((ratio) => Math.round(roulette.min_power + (roulette.max_power - roulette.min_power) * ratio)), battle.opponent.power].map((power) => `<span>${fmt(power)}</span>`).join('')}</div></div><p>${tr('battle.rouletteBody')}</p></div>` : '';
  return `<section class="battle-layout" aria-label="${tr('battle.screenTitle')}">
    <div class="battle-overview">
      <div class="battle-heading"><div><span class="eyebrow">${escapeHTML(modeName(battle.mode))}</span><h1 class="ui-headline">${tr('battle.screenTitle')}</h1></div><div class="battle-timer ui-panel ui-panel-inset"><span class="ui-title" id="battle-seconds">—</span><small> ${tr('common.seconds')}</small></div></div>
      <section class="battle-card ui-panel"><div class="battle-topline"><span class="live-tag"><span class="status-dot" aria-hidden="true"></span><span id="battle-phase">${tr(preparing ? 'battle.preparing' : 'battle.active')}</span></span><span>${legacy ? tr('battle.legacyRules') : free ? tr('battle.freeFight') : rich('battle.stake', { amount: money(battle.wager?.stake_minor) })}</span></div><div class="battle-duration-track ui-progress" aria-hidden="true"><div class="ui-progress-fill" id="battle-time-progress"></div></div>${reel}<div class="fighters" data-battle-id="${escapeHTML(battle.id)}">${fighter(battle.you, true)}<span class="vs ui-display" aria-hidden="true">VS</span>${fighter(battle.opponent, false)}${hitEffects}</div>${odds}<div class="battle-prize ui-info" id="battle-prize">${legacy ? tr('battle.legacyPrize') : free ? rich('battle.freePrize', { amount: money(battle.wager.win_payout_minor) }) : rich(battle.dynamic_payout ? 'battle.dynamicPrize' : 'battle.paidPrize', { amount: money(battle.wager?.win_payout_minor) })}</div></section>
    </div>
    <div class="battle-controls"><div class="tap-score"><span>${tr('battle.yourTaps')} <strong>${fmt(battle.you.taps)} / ${fmt(battle.tap_cap)}</strong></span><span>${tr('battle.opponentTaps')} <strong>${fmt(battle.opponent.taps)}</strong></span></div><button type="button" class="ui-button ui-primary battle-tap" aria-keyshortcuts="Space" aria-describedby="battle-caption" data-action="battle-tap" data-focus="battle-tap"${disabled(battleInputBlocked() || maxed || now() >= battle.ends_at || preparing)}><strong id="battle-tap-title">${rich(maxed ? 'battle.tapMaxed' : 'battle.tapActive')}</strong><span>${maxed ? tr('battle.tapsAccepted') : tr('battle.tapHint', { taps: fmt(battle.tap_cap) })}</span></button><p class="battle-caption" id="battle-caption"></p></div>
  </section>`;
}

function renderResult(battle) {
  if (!battle.result) return '';
  const result = battle.result;
  const probability = Number.isFinite(battle.win_probability) ? `${fmt(Math.round(battle.win_probability * 100))}%` : '—';
  return `<section class="last-battle-card ui-result${result.won ? '' : ' loss'}" data-tone="${result.won ? 'success' : 'error'}" data-battle-id="${escapeHTML(battle.id)}" aria-label="${tr('result.title')}"><div class="last-battle-heading"><h2 class="ui-title ui-result-heading">${tr('result.title')}</h2><span class="last-battle-outcome ui-outcome">${icon(result.won ? 'trophy' : 'feather')}${tr(result.won ? 'common.win' : 'common.loss')}</span></div><div class="last-battle-summary"><span class="last-battle-opponent">${icon(battle.opponent.is_bot ? 'bot' : 'swords')}${escapeHTML(fighterName(battle.opponent))} · ${tr(battle.opponent.is_bot ? 'common.botSuffix' : 'common.playerSuffix')}</span><strong class="last-battle-net ui-net${result.net_minor < 0 ? ' negative' : ''}" data-tone="${result.net_minor < 0 ? 'error' : 'success'}">${rich('result.net', { amount: signedMoney(result.net_minor) })}</strong></div><p class="last-battle-details ui-muted">${battle.wager ? `${tr('result.stake', { amount: money(battle.wager.stake_minor) })} · ` : ''}${tr('result.payout', { amount: money(result.payout_minor) })} · ${tr('result.xp', { amount: fmt(result.xp) })} · ${tr('result.chance', { probability })}${result.legacy ? ` · ${tr('result.legacyRules')}` : ''}</p></section>`;
}

function renderHistory() {
  const history = state.history || [];
  return `<div class="section-title arena-section-title"><h2 class="ui-title ui-section-title">${tr('history.title')}</h2><small>${tr('history.stats', { wins: fmt(state.player.wins), losses: fmt(state.player.losses) })}</small></div>${history.length ? `<div class="history-list ui-panel">${history.slice(0, 5).map((battle) => `<${battle.opponent.is_bot ? 'div' : 'button type="button"'} class="history-row${battle.opponent.is_bot ? '' : ' history-open ui-button'}"${battle.opponent.is_bot ? '' : ` data-action="open-result" data-battle-id="${escapeHTML(battle.id)}" data-focus="history-${escapeHTML(battle.id)}" aria-controls="battle-result-toast"`}><span class="history-mark ui-badge" data-tone="${battle.result?.won ? 'success' : 'error'}" aria-hidden="true">${icon(battle.result?.won ? 'trophy' : 'feather')}</span><div class="history-detail"><strong>${escapeHTML(fighterName(battle.opponent))}${battle.opponent.is_bot ? ` · ${tr('common.botSuffix')}` : ''}</strong><span>${tr(battle.result?.won ? 'common.win' : 'common.loss')} · ${escapeHTML(modeName(battle.mode))}</span>${battle.opponent.is_bot ? '' : `<span class="history-open-hint">${tr('history.openDetails')} ${icon('arrow-right')}</span>`}</div><span class="history-reward${battle.result?.net_minor < 0 ? ' negative' : ''}">${signedMoney(battle.result?.net_minor)} ${coin()}</span></${battle.opponent.is_bot ? 'div' : 'button'}>`).join('')}</div>` : `<div class="empty-state arena-history-empty ui-panel"><span aria-hidden="true">${icon('arena')}</span>${tr('history.emptyTitle')}<br>${tr('history.emptyBody')}</div>`}`;
}

/** Gear feedback follows the existing single, recoverable command; no optimistic economy. */
function gearCommandKey(command) {
  if (command?.path === '/gear/upgrade') return `slot-${command.body.slot}`;
  if (command?.path === '/breed/buy') return `breed-${command.body.breed_id}`;
  return null;
}
function recordGearFeedback(command, message, error = false) {
  const key = gearCommandKey(command);
  if (key) gearFeedback = { key, owner: command.owner, message, error };
}
function renderGearFeedback(key) {
  if (gearFeedback?.key !== key || gearFeedback.owner !== state.player.id) return '';
  return `<p class="gear-feedback ui-status${gearFeedback.error ? ' is-error' : ''}" data-tone="${gearFeedback.error ? 'error' : 'success'}" role="status">${escapeHTML(messageText(gearFeedback.message))}</p>`;
}

function gearAvailability(key, cost, complete = false) {
  if (complete) return 'complete';
  if (gearCommandKey(pending) === key) return busy ? 'loading' : 'retry';
  if (lockedGear()) return 'locked';
  if (cost !== null && state.player.balance_minor < cost) return 'funds';
  if (blocked()) return 'waiting';
  return 'ready';
}

/** Repeated purchase controls share cost formatting, reasons and recovery. */
function renderGearAction({ key, cost, status, action, attributes, label, primary = true }) {
  const reason = { locked: 'gear.unavailable', waiting: 'gear.waiting', loading: 'gear.processing', retry: 'gear.retryPurchase' }[status];
  return `<div class="gear-action">
    <div class="gear-price">${cost !== null ? `<span class="ui-muted">${tr('gear.cost')}</span><strong class="ui-title">${money(cost)} ${coin()}</strong>` : ''}</div>
    <button type="button" class="ui-button${primary ? ' ui-primary' : ' ui-accent'}" data-action="${status === 'retry' ? 'gear-retry' : action}" ${attributes} data-focus="${escapeHTML(key)}" aria-describedby="${escapeHTML(key)}-status" aria-busy="${status === 'loading'}"${disabled(!['ready', 'retry'].includes(status))}>${status === 'loading' ? '<span class="ui-spinner" aria-hidden="true"></span>' : ''}${tr(status === 'loading' ? 'gear.processing' : status === 'retry' ? 'common.retry' : label)}</button>
    <p class="gear-availability" id="${escapeHTML(key)}-status">${status === 'funds' ? rich('gear.needMore', { amount: money(cost - state.player.balance_minor) }) : reason ? tr(reason) : ''}</p>
  </div>`;
}

function renderUpgradeRow(slot) {
  const player = state.player;
  const cost = state.economy.upgrade_costs_minor[slot.id];
  const capped = cost === null;
  const level = player.gear[slot.id];
  const key = `slot-${slot.id}`;
  const status = gearAvailability(key, cost, capped);
  const name = i18n.has(`slot.${slot.id}`) ? t(`slot.${slot.id}`) : slot.name;
  return `<article class="upgrade-row ui-panel" data-slot-id="${escapeHTML(slot.id)}" data-state="${status}" aria-busy="${status === 'loading'}">
    <div class="gear-item-heading"><div class="gear-item-art ui-panel ui-panel-inset">${slotSVG(slot.id)}</div><div><h3 class="ui-title">${escapeHTML(name)}</h3><span class="ui-badge" data-tone="${capped ? 'success' : 'neutral'}">${tr('common.level', { level: fmt(level) })}</span></div></div>
    <p class="gear-benefit">${icon('power')}${tr('gear.currentBenefit', { power: fmt(level * slot.power_per_level) })}</p>
    ${capped ? `<p class="gear-complete ui-status" data-tone="success">${icon('check')}${tr('gear.maxLevel')}</p>` : `<p class="gear-next ui-muted">${tr('gear.nextBenefit', { level: fmt(level + 1), power: fmt(slot.power_per_level) })}</p>${renderGearAction({ key, cost, status, action: 'upgrade', attributes: `data-slot="${escapeHTML(slot.id)}"`, label: 'gear.upgrade' })}`}
    ${renderGearFeedback(key)}
  </article>`;
}

function renderBreedChoice(breed, frame) {
  const player = state.player;
  const equipped = player.breed_id === breed.id;
  const owned = player.owned_breeds.includes(breed.id);
  const key = `breed-${breed.id}`;
  const status = gearAvailability(key, owned ? null : breed.price_minor, equipped);
  const description = i18n.has(`breed.${breed.id}.description`) ? t(`breed.${breed.id}.description`) : breed.description;
  return `<article class="gear-breed ui-panel${equipped ? ' ui-panel-raised is-equipped' : ''}" data-breed-id="${escapeHTML(breed.id)}" data-state="${status}" aria-busy="${status === 'loading'}">
    <div class="gear-breed-heading"><div class="gear-breed-art ui-panel ui-panel-inset">${renderRooster({ breed_id: breed.id, gear: player.gear }, { label: breedName(breed.id), frame })}</div><div><span class="gear-ownership ui-badge" data-tone="${equipped ? 'success' : owned ? 'accent' : 'neutral'}">${rich(equipped ? 'gear.equipped' : owned ? 'gear.owned' : 'gear.notOwned')}</span><h3 class="ui-title">${escapeHTML(breedName(breed.id))}</h3><p class="gear-multiplier">${tr('gear.multiplier', { multiplier: fmt(breed.power_multiplier) })}</p></div></div>
    <p class="gear-description ui-muted">${escapeHTML(description)}</p>
    ${equipped ? '' : renderGearAction({ key, cost: owned ? null : breed.price_minor, status, action: 'breed', attributes: `data-breed="${escapeHTML(breed.id)}"`, label: owned ? 'gear.equip' : 'gear.buyEquip', primary: !owned })}
    ${renderGearFeedback(key)}
  </article>`;
}

function renderGear() {
  const player = state.player;
  // One frame for the catalog preserves the relative size of each breed.
  const breedFrame = roosterBattleFrame(state.catalog.breeds.map(breed => ({ breed_id: breed.id, gear: player.gear })));
  return `<section class="gear-screen ui-screen" aria-labelledby="gear-title">
    <div class="ui-screen-heading">${icon('gear')}<h1 class="ui-headline" id="gear-title">${tr('gear.screenTitle')}</h1></div>
    <div class="gear-fighter ui-panel ui-panel-raised"><div class="gear-fighter-art">${renderRooster(player, { label: t('a11y.rooster'), frame: roosterBattleFrame([player]), priority: true })}</div><div><p class="ui-muted">${tr('gear.currentFighter')}</p><h2 class="ui-title">${escapeHTML(breedName(player.breed_id))}</h2><div class="gear-fighter-stats"><span class="ui-badge" data-tone="neutral">${tr('common.level', { level: fmt(player.level) })}</span><strong>${icon('power')}${tr('arena.fighterPower', { power: fmt(player.power) })}</strong></div></div></div>
    ${lockedGear() ? `<p class="gear-lock ui-status" role="status">${tr('gear.locked')}</p>` : ''}
    ${blocked() && !lockedGear() ? `<p class="gear-lock ui-status" role="status">${tr(busy ? 'gear.commandPending' : 'gear.commandRetry')}</p>` : ''}
    <section aria-labelledby="gear-upgrades-title"><h2 class="ui-title ui-section-title" id="gear-upgrades-title">${tr('gear.upgradesTitle')}</h2><p class="gear-section-note ui-muted">${tr('gear.upgradesHint')}</p><div class="gear-upgrades equipment-grid">${state.catalog.slots.map(renderUpgradeRow).join('')}</div></section>
    <section class="gear-breeds" aria-labelledby="gear-breeds-title"><h2 class="ui-title ui-section-title" id="gear-breeds-title">${tr('gear.breeds')}</h2><p class="gear-section-note ui-muted">${tr('gear.intro')}</p><div class="gear-breed-list">${state.catalog.breeds.map(breed => renderBreedChoice(breed, breedFrame)).join('')}</div></section>
  </section>`;
}

/** Roost presentation only. Amounts, availability and dates always come from state. */
function roostCommandKey(command) {
  return { '/claim/daily': 'daily', '/claim/passive': 'passive' }[command?.path] || null;
}
function recordRoostFeedback(key, message, error = false, owner = state.player.id, amount = null) {
  roostFeedback[key] = { message, error, owner, amount };
}
function renderRoostFeedback(key) {
  const feedback = roostFeedback[key];
  if (!feedback || feedback.owner !== state.player.id) return '';
  if (key === 'daily' && !feedback.error && state.economy.daily_available) return '';
  const message = feedback.amount == null ? feedback.message : { key: 'roost.collected', params: { amount: money(feedback.amount) } };
  return `<p class="roost-feedback ui-status${feedback.error ? ' is-error' : ''}" data-tone="${feedback.error ? 'error' : 'success'}" role="status">${escapeHTML(messageText(message))}</p>`;
}
function renderRoostReward(kind) {
  const daily = kind === 'daily';
  const economy = state.economy;
  const amount = daily ? economy.daily_reward_minor : economy.passive_available_minor;
  const available = daily ? economy.daily_available : amount > 0;
  // Allow very large amounts to wrap between digit groups, not inside a group.
  const amountText = escapeHTML(money(amount)).replace(/([,\u00a0\u202f])(?=\d{3}(?:[,\u00a0\u202f.]|$))/g, '$1<wbr>');
  const ownPending = roostCommandKey(pending) === kind;
  const feedback = roostFeedback[kind]?.owner === state.player.id ? roostFeedback[kind] : null;
  const status = ownPending ? (busy ? 'claiming' : 'retry') : feedback?.error ? 'error'
    : available ? 'ready' : feedback ? 'claimed' : 'waiting';
  const active = ownPending || available;
  const primary = active && (daily || (!economy.daily_available && roostCommandKey(pending) !== 'daily'));
  const action = ownPending && !busy ? 'roost-retry' : `claim-${kind}`;
  const label = status === 'claiming' ? 'roost.claiming' : status === 'retry' || (status === 'error' && available) ? 'common.retry'
    : available ? daily ? 'roost.claimNow' : 'roost.collectNow' : daily ? 'roost.alreadyClaimed' : 'roost.accumulating';
  const next = daily && !economy.daily_available;
  return `<section class="roost-reward roost-${kind} ui-panel${daily ? ' ui-panel-raised' : ''}" data-reward="${kind}" data-state="${status}" aria-busy="${status === 'claiming'}" aria-labelledby="roost-${kind}-title">
    <h2 class="ui-title" id="roost-${kind}-title">${tr(daily ? 'roost.dailyTitle' : 'roost.passiveTitle')}</h2>
    <div class="roost-treasure">${icon('coin')}<p class="roost-amount ui-display">${amountText} <span class="ui-muted">${tr('roost.coins')}</span></p></div>
    <p class="roost-description${daily ? ' ui-status' : ' ui-muted'}"${daily && available && !ownPending ? ' data-tone="success"' : ''} id="roost-${kind}-description">${daily ? icon(next ? 'check' : ownPending ? 'refresh' : 'coin') : ''}<span>${tr(daily ? next ? 'roost.nextAvailable' : ownPending ? 'roost.confirming' : 'roost.readyNow' : 'roost.savingsHint')}${next ? ` <strong>${escapeHTML(formatDate(economy.next_daily_at))}</strong>` : ''}</span></p>
    <button type="button" class="ui-button${primary ? ' ui-primary' : ''}" data-action="${action}" data-focus="claim-${kind}" aria-busy="${status === 'claiming'}" aria-describedby="roost-${kind}-description"${disabled(ownPending ? busy : blocked() || !available)}>${status === 'claiming' ? '<span class="ui-spinner" aria-hidden="true"></span>' : icon(status === 'retry' || status === 'error' ? 'refresh' : available ? 'coin' : daily ? 'check' : 'roost')}${tr(label)}</button>
    ${ownPending && !busy ? `<p class="roost-description ui-muted">${tr('roost.retrySafe')}</p>` : blocked() && !ownPending && available ? `<p class="roost-description ui-muted">${tr('roost.waitingCommand')}</p>` : ''}
    ${renderRoostFeedback(kind)}
  </section>`;
}
function renderRoostEvents() {
  const events = state.reward_events || [];
  return `<section class="roost-events ui-panel" aria-labelledby="roost-events-title"><h2 class="ui-title" id="roost-events-title">${tr('roost.eventsTitle')}</h2>
    ${events.length ? `<ol class="roost-event-list">${events.map(event => {
      const label = tr(event.reason === 'battle_reward' ? 'roost.eventBattle' : event.reason === 'referral_signup' ? 'roost.eventSignup' : 'roost.eventReferral');
      const amount = escapeHTML(signedMoney(event.amount_minor)).replace(/([,\u00a0\u202f])(?=\d{3}(?:[,\u00a0\u202f.]|$))/g, '$1<wbr>');
      return `<li class="roost-event" data-reward-event="${escapeHTML(event.id)}"><div class="roost-event-main"><strong>${label}</strong>${event.friend_name ? `<span class="roost-event-friend">${escapeHTML(event.friend_name)}</span>` : ''}<time class="roost-event-time ui-muted">${escapeHTML(formatDate(event.created_at))}</time></div><span class="roost-event-amount"><span>${amount}</span>${coin()}</span></li>`;
    }).join('')}</ol><p class="roost-events-hint ui-muted">${tr('roost.eventsHint')}</p>` : `<p class="ui-muted">${tr('roost.eventsEmpty')}</p>`}
  </section>`;
}
function renderRoost() {
  const { player } = state;
  const breed = state.catalog.breeds.find(item => item.id === player.breed_id);
  const referral = state.catalog.referral;
  const referralAvailable = Boolean(config.bot_username && !player.is_dev);
  const statValue = value => escapeHTML(fmt(value)).replace(/([,\u00a0\u202f])(?=\d{3}(?:[,\u00a0\u202f.]|$))/g, '$1<wbr>');
  return `<section class="roost-screen ui-screen" aria-labelledby="roost-title">
    <header class="roost-heading"><div><h1 class="ui-headline ui-screen-heading" id="roost-title">${icon('roost')}${tr('roost.screenTitle')}</h1><p class="roost-home-label ui-muted">${escapeHTML(breedName(player.breed_id))} <span class="ui-badge">${tr('common.level', { level: fmt(player.level) })}</span></p></div><div class="roost-portrait">${roosterSVG('roost-home', breed?.color)}</div></header>
    ${renderRoostReward('daily')}
    ${renderRoostReward('passive')}
    <section class="roost-progress" aria-labelledby="roost-progress-title"><h2 class="ui-title ui-section-title" id="roost-progress-title">${tr('roost.progressTitle')}</h2><dl>
      <div class="roost-xp ui-panel"><dt>${icon('feather')}${tr('roost.experience')}</dt><dd><span class="ui-display">${fmt(player.xp_in_level)} / ${fmt(player.xp_to_next)}</span><div class="ui-progress" role="progressbar" aria-label="${tr('a11y.xp')}" aria-valuemin="0" aria-valuenow="${Number(player.xp_in_level)}" aria-valuemax="${Number(player.xp_to_next)}"><span style="width:${clamp(player.xp_in_level / Math.max(1, player.xp_to_next) * 100, 0, 100)}%"></span></div></dd></div>
      <div class="roost-power ui-panel ui-panel-raised"><dt>${icon('power')}${tr('roost.power')}</dt><dd class="ui-display">${fmt(player.power)}</dd></div>
      <div class="roost-stat ui-panel"><dt>${icon('swords')}${tr('roost.battles')}</dt><dd class="ui-display">${statValue(player.battles)}</dd></div>
      <div class="roost-stat ui-panel"><dt>${icon('trophy')}${tr('roost.pvpWins')}</dt><dd class="ui-display">${statValue(player.pvp_wins)}</dd></div>
    </dl></section>
    <section class="roost-referral ui-panel" aria-labelledby="roost-referral-title"><h2 class="ui-title" id="roost-referral-title">${tr('roost.inviteTitle')}</h2><p class="ui-muted">${tr('roost.referralBody', { signupReward: money(referral.signup_reward_minor), battleReward: money(referral.battle_reward_minor), battles: fmt(referral.battles_required) })}</p>
      ${referralAvailable ? `<div class="roost-invite-actions"><button type="button" class="ui-button ui-accent" data-action="invite" data-focus="roost-invite">${rich('roost.invite')}</button><button type="button" class="ui-button" data-action="copy-invite" data-focus="roost-copy" aria-busy="${roostCopying}"${disabled(roostCopying)}>${roostCopying ? '<span class="ui-spinner" aria-hidden="true"></span>' : icon('document')}${tr(roostCopying ? 'roost.copying' : 'roost.copyInvite')}</button></div>${renderRoostFeedback('invite')}` : `<p class="roost-description ui-status">${icon('info')}<span>${tr(player.is_dev ? 'roost.telegramOnly' : 'roost.botSetup')}</span></p>`}
    </section>${renderRoostEvents()}<p class="roost-footnote ui-info">${icon('info')}<span>${tr('roost.playMoney')}</span></p>
  </section>`;
}

function formatDate(timestamp) { return i18n.formatDate(timestamp); }

function renderLeaderboard() {
  const players = leaderboard?.[ranking];
  const hasResults = Boolean(players?.length);
  const ownIndex = players?.findIndex(player => player.id === state.player.id) ?? -1;
  const metric = ranking === 'power' ? 'leaderboard.power' : 'leaderboard.rankingWins';
  const refreshIcon = icon('refresh');
  let status = '';
  if (leaderboardLoading) status = tr(leaderboard ? 'leaderboard.refreshing' : 'leaderboard.loading');
  else if (leaderboardError) status = tr(leaderboard ? 'leaderboard.stale' : 'leaderboard.failed');
  else if (lastLeaderboard) status = tr('leaderboard.updated', { time: new Date(lastLeaderboard).toLocaleTimeString(i18n.locale(), { hour: '2-digit', minute: '2-digit' }) });
  let content;
  if (hasResults) {
    content = `<div class="rankings-columns" aria-hidden="true"><span>#</span><span>${tr('leaderboard.fighter')}</span><span>${tr(metric)}</span></div>
      <ol class="leaderboard-table rankings-list" aria-label="${tr('a11y.leaderboard')}">${players.map((player, index) => {
        const own = player.id === state.player.id;
        return `<li class="rankings-row ui-panel${index < 3 ? ' is-leading' : ''}${own ? ' is-you ui-result' : ''}${fmt(player[ranking]).length > 9 ? ' has-long-value' : ''}"${own ? ' id="rankings-self" tabindex="-1"' : ''}><span class="rankings-rank ui-display">${fmt(index + 1)}</span><span class="leaderboard-name">${escapeHTML(player.name)}${own ? `<small class="ui-badge" data-tone="accent">${icon('check')}${tr('leaderboard.you')}</small>` : ''}</span><strong class="rankings-value ui-display"><span class="rankings-sr-only">${tr(metric)}: </span>${fmt(player[ranking])}</strong></li>`;
      }).join('')}</ol>`;
  } else if (leaderboardLoading) {
    content = `<div class="rankings-skeleton" aria-hidden="true">${Array.from({ length: 5 }, () => '<div class="ui-panel"><i></i><i></i><i></i></div>').join('')}</div>`;
  } else if (leaderboardError) {
    content = `<div class="rankings-empty ui-panel">${icon('alert')}<h2 class="ui-title">${tr('leaderboard.unavailable')}</h2><p class="ui-muted">${tr('leaderboard.tryAgain')}</p></div>`;
  } else {
    content = `<div class="rankings-empty ui-panel">${icon('trophy')}<h2 class="ui-title">${tr('leaderboard.emptyTitle')}</h2><p class="ui-muted">${tr('leaderboard.empty')}</p></div>`;
  }
  // Positions come only from the server-ordered list. No rank is inferred outside it.
  const ownSummary = !hasResults ? '' : ownIndex >= 5
    ? `<button type="button" class="rankings-own ui-button ui-accent" data-action="ranking-self" data-focus="ranking-self"><strong>${tr('leaderboard.yourPosition', { rank: fmt(ownIndex + 1) })}</strong><span>${tr('leaderboard.showPosition')} ${icon('arrow-down')}</span></button>`
    : ownIndex < 0 ? `<p class="rankings-outside ui-info">${icon('info')}<span>${tr('leaderboard.outside')}</span></p>` : '';
  return `<section class="rankings-screen ui-screen" aria-labelledby="rankings-title">
    <header class="rankings-heading ui-screen-heading">${icon('trophy')}<h1 id="rankings-title" class="ui-headline">${tr('nav.leaderboard')}</h1><button type="button" class="rankings-refresh ui-button" data-action="refresh-leaderboard" data-focus="refresh-leaderboard" aria-label="${tr('leaderboard.refresh')}" title="${tr('leaderboard.refresh')}" aria-busy="${leaderboardLoading}"${disabled(leaderboardLoading)}>${leaderboardLoading ? '<span class="ui-spinner" aria-hidden="true"></span>' : refreshIcon}</button></header>
    <div class="rankings-modes ui-segmented" role="group" aria-label="${tr('a11y.ranking')}">${['power', 'pvp_wins'].map(mode => `<button type="button" class="ui-button${mode === 'pvp_wins' ? ' ui-accent' : ''}" data-action="ranking" data-ranking="${mode}" data-focus="ranking-${mode}" aria-pressed="${ranking === mode}"><span class="ui-selection">${icon('check')}</span>${icon(mode === 'power' ? 'power' : 'swords')}<span class="rankings-mode-label">${tr(mode === 'power' ? 'leaderboard.rankingPower' : 'leaderboard.rankingWins')}</span></button>`).join('')}</div>
    <p class="rankings-status${leaderboardLoading || leaderboardError ? ' ui-status' : ' ui-muted'}${leaderboardError ? ' is-error' : ''}"${leaderboardError ? ' data-tone="error"' : ''} role="status" aria-live="polite">${status}${leaderboardError ? ` <button type="button" class="ui-button" data-action="refresh-leaderboard" data-focus="rankings-retry">${tr('common.retry')}</button>` : ''}</p>
    ${ownSummary}<div class="rankings-results" aria-busy="${leaderboardLoading}">${content}</div>
    <p class="rankings-note ui-info">${icon('info')}<span>${tr(state.player.is_dev ? 'leaderboard.devNote' : 'leaderboard.note')}</span></p>
  </section>`;
}

async function loadLeaderboard() {
  if (leaderboardLoading || !token) return;
  leaderboardLoading = true;
  leaderboardError = '';
  if (tab === 'leaderboard') render();
  try { leaderboard = await request('/leaderboard'); lastLeaderboard = Date.now(); }
  catch (error) { leaderboardError = errorMessage(error); }
  finally { leaderboardLoading = false; if (tab === 'leaderboard') render(); }
}

function updateTimers() {
  updateResultToastTimer();
  if (tab !== 'arena' || !state) return;
  if (activeBattle()) {
    const battle = state.battle;
    const current = now();
    const preparing = current < battle.starts_at;
    const duration = Math.max(1, battle.ends_at - battle.starts_at);
    const remaining = Math.max(0, (preparing ? battle.starts_at : battle.ends_at) - current);
    const ended = current >= battle.ends_at;
    if (ended) clearBattleHit();
    const maxed = battle.you.taps >= battle.tap_cap;
    if ($('#battle-seconds')) $('#battle-seconds').textContent = fmt(Math.ceil(remaining));
    if ($('#battle-time-progress')) $('#battle-time-progress').style.width = `${preparing ? 100 : clamp(remaining / duration * 100, 0, 100)}%`;
    if ($('#battle-phase')) $('#battle-phase').textContent = t(preparing ? 'battle.preparing' : ended ? 'battle.finished' : 'battle.active');
    const button = $('[data-action="battle-tap"]');
    if (button) button.disabled = preparing || ended || maxed || battleInputBlocked();
    const tapTitle = $('#battle-tap-title');
    const tapKey = preparing ? 'battle.tapPreparing' : ended ? 'battle.tapFinished' : maxed ? 'battle.tapMaxed' : 'battle.tapActive';
    const titleVersion = `${i18n.getLanguage()}:${tapKey}`;
    if (tapTitle && tapTitle.dataset.content !== titleVersion) {
      tapTitle.innerHTML = rich(tapKey);
      tapTitle.dataset.content = titleVersion;
    }
    if ($('#roulette')) $('#roulette').hidden = !preparing;
    if ($('#battle-odds')) $('#battle-odds').hidden = preparing || !Number.isFinite(battle.current_win_probability);
    if ($('[data-power-value=opponent]')) $('[data-power-value=opponent]').textContent = preparing && battle.roulette ? '—' : fmt(battle.opponent?.power);
    if ($('#battle-caption')) $('#battle-caption').textContent = preparing
      ? t('battle.captionPreparing', { seconds: fmt(duration) })
      : ended ? t('battle.captionFinished')
      : t(maxed ? 'battle.captionMaxed' : pending?.path === '/battle/tap' && !busy
        ? 'battle.captionReconnecting' : battleBuffer || pending?.path === '/battle/tap'
          ? 'battle.captionSending' : 'battle.captionCombat');
    feedback.battle(battle, preparing ? 'preparing' : ended ? 'ended' : 'active');
  }
  if (state.queue && $('#queue-time')) {
    const elapsed = Math.max(0, Math.floor(now() - state.queue.joined_at));
    $('#queue-time').textContent = t('queue.elapsed', { time: `${fmt(Math.floor(elapsed / 60))}:${String(elapsed % 60).padStart(2, '0')}` });
  }
}

function goTo(nextTab) {
  if (!['arena', 'gear', 'roost', 'leaderboard'].includes(nextTab)) return;
  if (nextTab !== tab) arenaHelpMode = null;
  tab = nextTab;
  try { history.replaceState(null, '', `#${tab}`); } catch { /* Embedded origin may disallow history changes. */ }
  render();
  main.classList.remove('page-in');
  void main.offsetWidth;
  main.classList.add('page-in');
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (tab === 'leaderboard' && (!leaderboard || Date.now() - lastLeaderboard > 15000)) loadLeaderboard();
}

function referralURL() {
  return `https://t.me/${encodeURIComponent(config.bot_username.replace(/^@/, ''))}?startapp=${encodeURIComponent(state.player.referral_code)}`;
}

async function handleAction(action, button) {
  if (action === 'open-guide') { guide.open(); return; }
  if (action === 'close-guide') { guide.close(); return; }
  if (guide.isOpen()) return;
  if (action === 'close-result') { closeResultToast(); syncGuide(); return; }
  if (action === 'close-mode-help') { closeModeHelp(); return; }
  if (action === 'retry' || action === 'gear-retry' || action === 'roost-retry') return sendPending();
  if (action === 'dismiss-notice') return clearNotice();
  if (action === 'boot-retry') return boot();
  if (!state) return;
  if (action === 'open-result') {
    if (activeBattle() || state.queue) return;
    const battle = state.history?.find(item => item.id === button.dataset.battleId);
    if (!battle?.result || battle.opponent.is_bot) return;
    closeResultToast();
    resultToast = battle;
    resultToastUntil = Infinity;
    resultReturnBattleId = battle.id;
    renderResultToast();
    $('#battle-result-toast').querySelector('[data-action="close-result"]')?.focus({ preventScroll: true });
    syncGuide();
    return;
  }
  if (action === 'retry-stake-quote') return loadStakeQuote();
  if (action === 'stake-range') { selectStake(Number(button.value)); return; }
  if (action === 'mode-help') {
    if (tab === 'arena' && !activeBattle() && !state.queue && ['bot', 'online'].includes(button.dataset.mode)) {
      arenaHelpMode = arenaHelpMode === button.dataset.mode ? null : button.dataset.mode;
      render();
    }
    return;
  }
  if (action === 'go-gear') return goTo('gear');
  if (action === 'go-arena') return goTo('arena');
  if (action === 'refresh-leaderboard') return loadLeaderboard();
  if (action === 'ranking-self') {
    const ownRow = $('#rankings-self');
    ownRow?.scrollIntoView({ block: 'center', behavior: 'instant' });
    ownRow?.focus({ preventScroll: true });
    return;
  }
  if (action === 'ranking') {
    if (['power', 'pvp_wins'].includes(button.dataset.ranking)) { ranking = button.dataset.ranking; render(); }
    return;
  }
  if (action === 'stake') {
    const value = Number(button.dataset.stakeMinor);
    if (state.catalog.battle.stakes_minor.includes(value)) selectStake(value);
    return;
  }
  if (action === 'arena-mode') {
    if (!blocked() && ['bot', 'online'].includes(button.dataset.mode)) {
      selectedMode = button.dataset.mode;
      arenaHelpMode = null;
      storage.set('arenaMode', selectedMode);
      render();
    }
    return;
  }
  if (action === 'battle-tap') {
    if (!activeBattle() || state.battle.you.taps >= state.battle.tap_cap || now() >= state.battle.ends_at || now() < state.battle.starts_at || battleInputBlocked()) return;
    bufferedBattleId = state.battle.id;
    battleBuffer = Math.min(state.battle.tap_cap, battleBuffer + 1);
    animateBattleHit();
    return;
  }
  if (action === 'invite') {
    const share = `https://t.me/share/url?url=${encodeURIComponent(referralURL())}&text=${encodeURIComponent(t('roost.shareText'))}`;
    try {
      if (telegram?.openTelegramLink) telegram.openTelegramLink(share);
      else window.open(share, '_blank', 'noopener,noreferrer');
      recordRoostFeedback('invite', { key: 'roost.shareOpened' });
    } catch { recordRoostFeedback('invite', { key: 'roost.shareFailed' }, true); }
    if (tab === 'roost') render();
    return;
  }
  if (action === 'copy-invite') {
    if (roostCopying) return;
    roostCopying = true;
    delete roostFeedback.invite;
    if (tab === 'roost') render();
    try {
      await navigator.clipboard.writeText(referralURL());
      recordRoostFeedback('invite', { key: 'notice.inviteCopied' });
      showNotice({ key: 'notice.inviteCopied' });
    } catch {
      recordRoostFeedback('invite', { key: 'notice.inviteCopyFailed' }, true);
      showNotice({ key: 'notice.inviteCopyFailed' }, { error: true });
    } finally { roostCopying = false; if (tab === 'roost') render(); }
    return;
  }
  if (blocked()) return;
  if (action === 'start-bot' || action === 'queue-join') {
    const quote = currentStakeQuotes()[action === 'start-bot' ? 'bot' : 'online'];
    if (currentStake() > stakeLimits().max_minor || !quote) return;
  }
  const actions = {
    'start-free': ['/battle/start', { mode: 'bot', stake_minor: 0, expected_power: state.player.power }],
    'start-bot': ['/battle/start', { mode: 'bot', stake_minor: currentStake(), expected_power: state.player.power }],
    'queue-join': ['/queue/join', { stake_minor: currentStake() }],
    'queue-leave': ['/queue/leave', {}],
    'claim-daily': ['/claim/daily', {}, { messageKey: 'notice.dailyClaimed' }],
    'claim-passive': ['/claim/passive', {}, { messageKey: 'notice.passiveClaimed' }],
    upgrade: ['/gear/upgrade', { slot: button.dataset.slot }, { messageKey: 'notice.gearUpgraded' }],
    breed: ['/breed/buy', { breed_id: button.dataset.breed }, { messageKey: 'notice.breedChanged' }],
  };
  if (actions[action]) {
    const [path, body, options] = actions[action];
    const success = await mutate(path, body, options);
    if (success && (path === '/battle/start' || path === '/queue/join')) { goTo('arena'); lastPoll = 0; }
  }
}

const battleInput = createBattleInput({
  pointerEvents: typeof window.PointerEvent === 'function',
  onTap: button => handleAction('battle-tap', button).catch(() => showNotice({ key: 'notice.actionFailed' }, { error: true })),
});
document.addEventListener('pointerdown', battleInput.pointerDown);
document.addEventListener('click', (event) => {
  const languageButton = event.target.closest('[data-language]');
  if (languageButton) return changeLanguage(languageButton.dataset.language);
  const tabButton = event.target.closest('[data-tab]');
  if (tabButton) return goTo(tabButton.dataset.tab);
  const button = event.target.closest('[data-action]');
  if (button?.dataset.action === 'battle-tap') return battleInput.click(event);
  if (button && !button.disabled) handleAction(button.dataset.action, button).catch(() => showNotice({ key: 'notice.actionFailed' }, { error: true }));
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'arena-stake-range') selectStake(Number(event.target.value));
});

/** Space uses the same combat path as a tap, including timing, cap and effects.
 * Own both key events so a focused button cannot add a native click on keyup.
 * Leave text entry and other controls with their normal keyboard behavior.
 */
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && resultToast && !guide.isOpen()) {
    event.preventDefault();
    closeResultToast();
    syncGuide();
    return;
  }
  if (event.key === 'Escape' && arenaHelpMode && tab === 'arena') {
    event.preventDefault();
    closeModeHelp();
    return;
  }
  if (event.code !== 'Space' && event.key !== ' ') return;
  if (event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (document.hidden || tab !== 'arena' || !activeBattle()) return;
  const target = event.target;
  if (target?.isContentEditable || target?.closest?.('input, textarea, select, [role="textbox"]')) return;
  const control = target?.closest?.('button, a, summary, [role="button"], [role="link"], [role="checkbox"], [role="switch"]');
  if (control && control.dataset.action !== 'battle-tap') return;
  event.preventDefault();
  spaceHandled = true;
  if (spaceHeld || event.repeat) return;
  spaceHeld = true;
  handleAction('battle-tap').catch(() => showNotice({ key: 'notice.actionFailed' }, { error: true }));
});
document.addEventListener('keyup', (event) => {
  if (event.code !== 'Space' && event.key !== ' ') return;
  if (spaceHandled) event.preventDefault();
  spaceHeld = false;
  spaceHandled = false;
});
window.addEventListener('blur', () => { spaceHeld = false; spaceHandled = false; });
document.querySelector('.brand').addEventListener('click', (event) => { event.preventDefault(); if (state) goTo('arena'); });
environment.onResume(() => {
  lastPoll = 0; lastPresence = 0;
  if (booting) return;
  if (pending && !busy && !pending.recoveryBlocked && combatRecoveryCommand()) sendPending();
  else refreshState();
});
window.addEventListener('online', () => { if (pending && !busy) sendPending(); else refreshState(); });
window.addEventListener('offline', () => setConnection(false));

/** Batch clicks without inventing accepted taps or optimistic currency values. */
setInterval(async () => {
  if (!state || blocked() || document.hidden) return;
  if (battleBuffer && activeBattle()) {
    const taps = battleBuffer;
    battleBuffer = 0;
    if (now() < state.battle.ends_at) await mutate('/battle/tap', { battle_id: bufferedBattleId, taps }, { quiet: true });
  }
}, 400);

setInterval(() => {
  updateTimers();
  if (!state || document.hidden || busy || booting) return;
  const interval = activeBattle() || state.queue ? 2000 : 10000;
  if (Date.now() - lastPoll >= interval) refreshState();
  if (!pending && !refreshing && Date.now() - lastPresence >= 15000) {
    lastPresence = Date.now();
    mutate('/presence', {}, { quiet: true });
  }
}, 250);

async function boot() {
  if (booting) return;
  booting = true;
  bootFailure = null;
  if (!state) {
    main.innerHTML = `<section class="loading-view ui-panel" role="status" aria-busy="true"><span class="loading-rooster" aria-hidden="true">${icon('rooster')}</span><h1 class="ui-headline" data-i18n="loading.title">${tr('loading.title')}</h1><p class="ui-muted" data-i18n="loading.body">${tr('loading.body')}</p><div class="loading-bar ui-progress" aria-hidden="true"><span></span></div></section>`;
  }
  try {
    environment.start();
    config = await request('/config', { auth: false });
    const context = currentAuthContext();
    if (token && context && storage.get('authContext') === context) {
      // A Telegram launch payload expires sooner than our signed session.
      // Reopening the same document can reuse that still-valid session.
      acceptState(await request('/state'));
    } else {
      await authenticate();
    }
    lastPoll = Date.now();
    if (pending) await sendPending();
    else { lastPresence = Date.now(); await mutate('/presence', {}, { quiet: true }); }
    if (tab === 'leaderboard') loadLeaderboard();
  } catch (error) {
    setConnection(false);
    bootFailure = error;
    renderBootFailure();
  } finally { booting = false; syncGuide(); }
}

updateStaticLanguage();
boot();
