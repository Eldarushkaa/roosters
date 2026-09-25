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
let battleBuffer = 0;
let bufferedBattleId = null;
let leaderboard = null;
let leaderboardLoading = false;
let leaderboardError = '';
let devIdentity = null;
let selectedStake = Number(storage.get('stakeMinor')) || null;
let selectedMode = storage.get('arenaMode') === 'online' ? 'online' : 'bot';
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

function closeResultToast() {
  clearTimeout(resultToastTimer);
  resultToastTimer = null;
  resultToast = null;
  const toast = $('#battle-result-toast');
  if (toast?.contains?.(document.activeElement)) main.focus({ preventScroll: true });
  if (toast) toast.hidden = true;
}

function acceptResultToast(next) {
  const completed = next.battle?.status === 'finished' && next.battle.result ? next.battle
    : next.history?.find(item => item.status === 'finished' && item.result);
  if (state?.player.id !== next.player.id) closeResultToast();
  if (!completed) return;
  const key = `resultToast.${next.player.id}`;
  if (seenResultToasts.get(next.player.id) === completed.id || storage.get(key) === completed.id) return;
  seenResultToasts.set(next.player.id, completed.id);
  storage.set(key, completed.id);
  // History stays available; only newly observed results get an expiring notice.
  if (next.battle?.status === 'active' || next.queue) return;
  closeResultToast();
  resultToast = completed;
  resultToastUntil = Date.now() + 4000;
  resultToastTimer = setTimeout(closeResultToast, 4000);
}

function renderResultToast() {
  const toast = $('#battle-result-toast');
  if (!toast || !resultToast) return;
  if (Date.now() >= resultToastUntil) { closeResultToast(); return; }
  const result = resultToast.result;
  toast.hidden = false;
  toast.classList.toggle('loss', !result.won);
  toast.innerHTML = `<button type="button" class="result-toast-close" data-action="close-result" aria-label="${tr('result.close')}"><svg viewBox="0 0 44 44" aria-hidden="true"><circle class="result-close-track" cx="22" cy="22" r="19"/><circle id="result-close-progress" cx="22" cy="22" r="19" pathLength="100"/></svg>${icon('close')}</button><span class="result-toast-mark" aria-hidden="true">${icon(result.won ? 'trophy' : 'feather')}</span><h2>${tr(result.won ? 'common.win' : 'common.loss')}</h2><strong>${rich('result.net', { amount: signedMoney(result.net_minor) })}</strong><p>${tr('result.payout', { amount: money(result.payout_minor) })} · ${tr('result.xp', { amount: fmt(result.xp) })}</p><small>${tr('result.autoClose')}</small>`;
  updateResultToastTimer();
}

function updateResultToastTimer() {
  if (!resultToast) return;
  if (Date.now() >= resultToastUntil) { closeResultToast(); return; }
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

async function request(path, { body, key, auth = true, retryAuth = true } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
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
      return request(path, { body, key, auth, retryAuth: false });
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
  if (pending.owner !== state.player.id) {
    pending = null;
    storage.remove('pending');
    return false;
  }
  const command = pending;
  command.attempts = (command.attempts || 0) + 1;
  storage.set('pending', JSON.stringify(command));
  if (gearCommandKey(command)) gearFeedback = null;
  const roostKey = roostCommandKey(command);
  if (roostKey) delete roostFeedback[roostKey];
  busy = true;
  stateVersion += 1;
  render();
  try {
    const response = await request(command.path, { body: command.body, key: command.key });
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
      showNotice({ key: 'notice.retrySafe', params: { message: errorMessage(error) } }, { error: true, retry: true });
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
  $('#notice-stack').innerHTML = `<div class="notice${options.error ? ' error' : ''}"><span>${escapeHTML(messageText(message))}</span>${options.retry ? `<button type="button" class="btn small" data-action="retry">${tr('common.retry')}</button>` : `<button type="button" class="dismiss" data-action="dismiss-notice" aria-label="${tr('common.dismissNotice')}">${icon('close')}</button>`}</div>`;
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
  main.innerHTML = `<section class="error-view"><span class="loading-rooster" aria-hidden="true">${icon(bootFailure ? 'alert' : 'rooster')}</span><h1>${tr(bootFailure.code === 'telegram_required' ? 'boot.telegramTitle' : 'boot.unavailableTitle')}</h1><p>${escapeHTML(messageText(errorMessage(bootFailure)))}</p><button type="button" class="btn primary" data-action="boot-retry">${rich('boot.retry')}</button></section>`;
}
function now() { return (Date.now() + serverOffset) / 1000; }
function blocked() { return busy || Boolean(pending); }
function activeBattle() { return state?.battle?.status === 'active'; }
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
    helmet: '<path d="M12 40v-9C12 18 20 10 32 10s20 8 20 21v9H38v12H26V40Z" fill="#819996" stroke="#c7d3ba" stroke-width="3"/><path d="M31 10v24M17 31h11m8 0h11" stroke="#253c45" stroke-width="5"/><path d="m25 8 7-7 7 7" fill="#ef9566"/>',
    armor: '<path d="m20 11 12 7 12-7 13 14-10 10-5-5v23H22V30l-5 5L7 25Z" fill="#728f92" stroke="#c7d3ba" stroke-width="3"/><path d="m24 20 8 4 8-4v20l-8 7-8-7Z" fill="#344e58"/><path d="M32 24v18" stroke="#e3bb74" stroke-width="3"/>',
    sword: '<path d="m20 43 9-16L47 7l9-3-2 10-20 22-11 10Z" fill="#b8cbc4" stroke="#e2ead6" stroke-width="2"/><path d="m14 35 17 16" stroke="#e8b560" stroke-width="6" stroke-linecap="round"/><path d="m21 44-9 11" stroke="#967553" stroke-width="8" stroke-linecap="round"/><path d="m30 29 19-19" stroke="#fff6d0" stroke-width="2"/>',
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
  const helpOpen = $('.battle-help')?.open || false;
  if (battling && !$('.app-shell').classList.contains('battle-mode')) window.scrollTo({ top: 0, behavior: 'instant' });
  $('.app-shell').classList.toggle('battle-mode', battling);
  const arenaHome = tab === 'arena' && !battling && !state.queue;
  $('.app-shell').classList.toggle('arena-home', arenaHome);
  $('.app-shell').classList.toggle('arena-returning', arenaHome && !state.economy.first_free_battle_available);
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
  const ongoing = tab !== 'arena' && (activeBattle() || state.queue) ? `<div class="context-banner"><span>${rich(activeBattle() ? 'context.battle' : 'context.queue')}</span><button class="btn small" type="button" data-action="go-arena">${rich('context.arena')}</button></div>` : '';
  main.innerHTML = ongoing + ({ arena: renderArena, gear: renderGear, roost: renderRoost, leaderboard: renderLeaderboard })[tab]();
  if ($('.battle-help')) $('.battle-help').open = helpOpen;
  if (keepFighters) $('.fighters').replaceWith(keepFighters);
  localizeFighters();
  if (focused) [...main.querySelectorAll('[data-focus]')].find((element) => element.dataset.focus === focused)?.focus({ preventScroll: true });
  renderResultToast();
  updateTimers();
  // The caption and restored fighters affect the scroll range. Restore only
  // after their final layout, so a poll cannot clamp a user's scroll to zero.
  if ($('.battle-overview')) $('.battle-overview').scrollTop = overviewScroll;
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
  for (const svg of document.querySelectorAll('.fighters svg[role="img"]')) svg.setAttribute('aria-label', t('a11y.rooster'));
}

function currentStake() {
  const stakes = state.catalog.battle.stakes_minor;
  if (!stakes.includes(selectedStake)) selectedStake = stakes[0];
  return selectedStake;
}

function renderStakeSelector() {
  return `<div class="stake-heading"><h3>${tr('arena.stakeTitle')}</h3><span>${tr('arena.gameCoins')}</span></div><div class="stake-options" role="group" aria-label="${tr('a11y.stake')}">${state.catalog.battle.stakes_minor.map((stake) => `<button type="button" class="stake-option" data-action="stake" data-stake-minor="${stake}" data-focus="stake-${stake}" aria-pressed="${stake === currentStake()}"${disabled(blocked())}><span class="selection-check">${icon('check')}</span>${money(stake)} ${coin()}</button>`).join('')}</div>`;
}

function renderArena() {
  const player = state.player;
  const battle = state.battle;
  const rules = state.catalog.battle;
  const breed = state.catalog.breeds.find((item) => item.id === player.breed_id);
  // The last completed result remains while a new search or fight is active.
  const completed = battle?.status === 'finished' && battle.result ? battle
    : state.history?.find((item) => item.status === 'finished' && item.result);
  const finished = completed ? renderResult(completed) : '';
  if (activeBattle()) return renderBattle(battle, finished);
  if (state.queue) return renderQueue() + finished;
  const free = state.economy.first_free_battle_available;
  const stake = currentStake();
  const quote = state.economy.bot_quotes.find((item) => item.stake_minor === stake);
  const pvpPayout = Math.round(stake * 2 * rules.pvp_pool_return);
  const cannotPay = player.balance_minor < stake;
  if (!free) return renderReturningArena({ player, rules, breed, stake, quote, pvpPayout, cannotPay, finished });
  const firstFight = free ? `<section class="first-fight"><span class="eyebrow">${tr('arena.firstEyebrow')}</span><h2>${tr('arena.firstTitle')}</h2><p>${tr('arena.firstBody', { taps: fmt(rules.tap_cap), seconds: fmt(rules.duration), reward: money(rules.free_reward_minor) })}</p><button type="button" class="btn primary full" data-action="start-free" data-focus="start-free"${disabled(blocked())}>${tr(blocked() ? 'arena.processing' : 'arena.firstAction')} ${icon('arrow-up-right')}</button><small>${tr('arena.firstFootnote')}</small></section>` : '';

  return `${finished}${firstFight}<section class="hero" aria-label="${tr('a11y.hero')}"><div class="hero-copy"><div class="hero-kicker">${tr('arena.heroKicker')}</div><h1>${tr('arena.heroTitleLine1')}<br>${tr('arena.heroTitleLine2')}</h1><p>${tr('arena.heroBody')}</p><div class="hero-footer"><span class="tag accent">${escapeHTML(breedName(player.breed_id))}</span><span class="tag">${tr('common.level', { level: fmt(player.level) })}</span></div></div><div class="rooster-stage">${roosterSVG('hero', breed?.color)}<div class="power-badge">${icon('power')}<div><span>${tr('arena.power')}</span><strong>${fmt(player.power)}</strong></div></div></div></section>
    <div class="level-strip"><span>${tr('common.level', { level: fmt(player.level) })}</span><div class="progress-track" role="progressbar" aria-label="${tr('a11y.xp')}" aria-valuenow="${Number(player.xp_in_level)}" aria-valuemax="${Number(player.xp_to_next)}"><div class="progress-fill" style="width:${clamp(player.xp_in_level / Math.max(1, player.xp_to_next) * 100, 0, 100)}%"></div></div><small>${fmt(player.xp_in_level)} / ${fmt(player.xp_to_next)} XP</small></div>
    <div class="section-title"><h2>${tr(free ? 'arena.onlineAlternative' : 'arena.readyTitle')}</h2>${presenceHTML()}</div>${renderStakeSelector()}
    <div class="arena-grid${free ? ' single-mode' : ''}"><article class="mode-card online"><span class="mode-index" aria-hidden="true">02</span><div class="mode-icon" aria-hidden="true">${icon('arena')}</div><h3>${tr('arena.onlineTitle')}</h3><p>${tr('arena.onlineBody')}</p><div class="wager-disclosure"><span>${tr('arena.yourStakeLabel')} <strong>${money(stake)} ${coin()}</strong></span><span>${tr('arena.winnerPayoutLabel')} <strong>${money(pvpPayout)} ${coin()}</strong></span><small>${tr('arena.onlineDisclosure', { percent: fmt(Math.round(rules.pvp_pool_return * 100)), searching: fmt(state.presence.searching) })}</small></div><button class="btn full" type="button" data-action="queue-join" data-focus="queue-join"${disabled(blocked() || cannotPay)}>${rich('arena.onlineAction', { stake: money(stake) })} <span class="btn-arrow" aria-hidden="true">${icon('arrow-up-right')}</span></button></article></div>
    ${cannotPay ? `<p class="muted-note">${tr('arena.insufficientFunds')}</p>` : ''}
    ${player.is_dev ? `<p class="muted-note">${tr('arena.devPresence', { count: fmt(state.presence.development_online) })}</p>` : ''}
    <details class="help-card"><summary>${tr('arena.helpTitle')}</summary><p>${tr('arena.helpTaps', { seconds: fmt(rules.duration), taps: fmt(rules.tap_cap) })}</p><p>${tr('arena.helpBot', { min: fmt(Math.round(rules.bot_rtp_min * 100)), max: fmt(Math.round(rules.bot_rtp_max * 100)), taps: fmt(rules.tap_cap) })}</p><p>${tr('arena.helpOnline')}</p></details>${renderHistory()}`;
}

function renderReturningArena({ player, rules, breed, stake, quote, pvpPayout, cannotPay, finished }) {
  const bot = selectedMode === 'bot';
  const unavailable = bot && !quote;
  const action = bot ? 'start-bot' : 'queue-join';
  const payout = bot ? (quote ? `${money(quote.min_payout_minor)}–${money(quote.max_payout_minor)}` : '—') : money(pvpPayout);
  return `<div class="arena-play">
    <section class="arena-fighter" aria-label="${tr('shell.yourFighter')}">
      <div class="arena-portrait">${roosterSVG('arena-fighter', breed?.color)}</div>
      <div class="arena-fighter-identity"><h1>${escapeHTML(breedName(player.breed_id))}</h1><p>${tr('common.level', { level: fmt(player.level) })} · ${tr('arena.fighterPower', { power: fmt(player.power) })}</p>
        <div class="arena-xp"><div class="progress-track" role="progressbar" aria-label="${tr('a11y.xp')}" aria-valuemin="0" aria-valuenow="${Number(player.xp_in_level)}" aria-valuemax="${Number(player.xp_to_next)}"><div class="progress-fill" style="width:${clamp(player.xp_in_level / Math.max(1, player.xp_to_next) * 100, 0, 100)}%"></div></div><span>${fmt(player.xp_in_level)} / ${fmt(player.xp_to_next)} XP</span></div>
      </div>
    </section>
    <section class="arena-setup" aria-label="${tr('arena.readyTitle')}">
      <h2 id="arena-mode-label">${tr('arena.modeTitle')}</h2>
      <div class="arena-modes" role="group" aria-labelledby="arena-mode-label">${['bot', 'online'].map((mode) => `<button type="button" class="stake-option" data-action="arena-mode" data-mode="${mode}" data-focus="mode-${mode}" aria-pressed="${selectedMode === mode}"${disabled(blocked())}><span class="selection-check">${icon('check')}</span>${tr(mode === 'bot' ? 'arena.botTitle' : 'arena.onlineTitle')}</button>`).join('')}</div>
      ${renderStakeSelector()}
      <div class="arena-risk" aria-live="polite">
        <div><span>${tr('arena.lossLabel')}</span><strong>−${money(stake)} ${coin()}</strong></div>
        <div><span>${tr('arena.winPayoutLabel')}</span><strong>${payout} ${coin()}</strong></div>
        <p>${tr(bot ? 'arena.botQuoteNote' : 'arena.onlineQuoteNote')}</p>
      </div>
      ${cannotPay || unavailable ? `<p class="arena-unavailable" role="status">${tr(cannotPay ? 'arena.insufficientFunds' : 'arena.quoteUnavailable')}</p>` : ''}
      <button class="btn primary full arena-fight" type="button" data-action="${action}" data-focus="${action}"${disabled(blocked() || cannotPay || unavailable)}>${rich(blocked() ? 'arena.processing' : bot ? 'arena.botAction' : 'arena.onlineFightAction', { stake: money(stake) })}${icon('arrow-up-right')}</button>
    </section>
    ${finished}
    <section class="arena-support" aria-label="${tr('arena.readyTitle')}">${presenceHTML()}<p>${tr(bot ? 'arena.botBody' : 'arena.onlineBody', { min: fmt(Math.round(rules.bot_power_min_ratio * 100)), max: fmt(Math.round(rules.bot_power_max_ratio * 100)) })}</p>
      ${!bot ? `<p>${tr('arena.onlineDisclosure', { percent: fmt(Math.round(rules.pvp_pool_return * 100)), searching: fmt(state.presence.searching) })}</p>` : ''}
      ${player.is_dev ? `<p>${tr('arena.devPresence', { count: fmt(state.presence.development_online) })}</p>` : ''}
    </section>
    <details class="help-card"><summary>${tr('arena.helpTitle')}</summary><p>${tr('arena.helpTaps', { seconds: fmt(rules.duration), taps: fmt(rules.tap_cap) })}</p><p>${tr('arena.helpBot', { min: fmt(Math.round(rules.bot_rtp_min * 100)), max: fmt(Math.round(rules.bot_rtp_max * 100)), taps: fmt(rules.tap_cap) })}</p><p>${tr('arena.helpOnline')}</p></details>${renderHistory()}
  </div>`;
}

function presenceHTML() {
  return `<div class="presence"><span class="status-dot" aria-hidden="true"></span><span>${tr('arena.presence', { count: fmt(state.presence.online) })}</span></div>`;
}

function renderQueue() {
  return `<div class="page-heading"><span class="eyebrow">${tr('queue.eyebrow')}</span><h1>${tr('queue.title')}</h1></div><section class="queue-card"><div class="radar" aria-hidden="true">${icon('arena')}</div><h2>${tr('queue.searching')}</h2><p>${tr('queue.body', { stake: money(state.queue.stake_minor) })}</p>${presenceHTML()}<div class="queue-time" id="queue-time"></div><p>${state.player.is_dev ? tr('queue.devBody', { count: fmt(state.presence.development_online) }) : tr('queue.stay')}</p><button type="button" class="btn" data-action="queue-leave" data-focus="queue-leave"${disabled(blocked())}>${tr('queue.leave')}</button></section>`;
}

function renderBattle(battle, finished = '') {
  const preparing = now() < battle.starts_at;
  const roulette = battle.mode === 'bot' && battle.roulette;
  const fighter = (item, mine) => {
    const side = mine ? 'you' : 'opponent';
    return `<div><div class="fighter-avatar${mine ? '' : ' opponent'}"><div class="fighter-motion" data-fighter="${side}">${roosterSVG(side, mine ? '#f5bc69' : '#afc8bb')}</div></div><strong class="fighter-name" data-fighter-name="${side}">${escapeHTML(fighterName(item))}</strong><div class="fighter-meta"><span data-fighter-role="${side}">${tr(mine ? 'battle.yourFighter' : item.is_bot ? 'battle.bot' : 'battle.player')}</span> · <b data-fighter-power="${side}"${mine ? '' : ' id="opponent-power"'}>${icon('power')}<span data-power-value="${side}">${!mine && preparing && roulette ? '—' : fmt(item.power)}</span></b></div></div>`;
  };
  const odds = `<div class="battle-odds" id="battle-odds"${preparing || !Number.isFinite(battle.current_win_probability) ? ' hidden' : ''}><div><span>${tr('battle.winChance')}</span><strong>${Number.isFinite(battle.current_win_probability) ? fmt(Math.round(battle.current_win_probability * 10000) / 100) + '%' : '—'}</strong></div></div>`;
  const hitEffects = '<div class="battle-hit-effects" aria-hidden="true"><span class="hit-slash"></span><span class="hit-spark"></span><span class="hit-spark"></span><span class="hit-spark"></span><span class="hit-spark"></span></div>';
  const maxed = battle.you.taps >= battle.tap_cap;
  const free = battle.wager?.stake_minor === 0;
  const legacy = ['v1', 'v2'].includes(battle.rules_version);
  const duration = Math.max(0.1, battle.starts_at - (battle.created_at ?? battle.starts_at - 2));
  const elapsed = clamp(now() - (battle.created_at ?? battle.starts_at - duration), 0, duration);
  const reel = roulette ? `<div class="roulette" id="roulette"${preparing ? '' : ' hidden'}><span class="eyebrow">${tr('battle.botPower', { min: fmt(roulette.min_power), max: fmt(roulette.max_power) })}</span><div class="roulette-window" aria-hidden="true"><div class="roulette-track" style="animation-duration:${duration}s;animation-delay:-${elapsed}s">${[...[0, .25, .5, .75, 1, .25].map((ratio) => Math.round(roulette.min_power + (roulette.max_power - roulette.min_power) * ratio)), battle.opponent.power].map((power) => `<span>${fmt(power)}</span>`).join('')}</div></div><p>${tr('battle.rouletteBody')}</p></div>` : '';
  return `<section class="battle-layout" aria-label="${tr('battle.screenTitle')}">
    <div class="battle-overview">
      <div class="battle-heading"><div><span class="eyebrow">${escapeHTML(modeName(battle.mode))}</span><h1>${tr('battle.screenTitle')}</h1></div><div class="battle-timer"><span id="battle-seconds">—</span><small> ${tr('common.seconds')}</small></div></div>
      <section class="battle-card"><div class="battle-topline"><span class="live-tag"><span class="status-dot"></span><span id="battle-phase">${tr(preparing ? 'battle.preparing' : 'battle.active')}</span></span><span>${legacy ? tr('battle.legacyRules') : free ? tr('battle.freeFight') : rich('battle.stake', { amount: money(battle.wager?.stake_minor) })}</span></div><div class="battle-duration-track"><div id="battle-time-progress"></div></div>${reel}<div class="fighters" data-battle-id="${escapeHTML(battle.id)}">${fighter(battle.you, true)}<span class="vs" aria-hidden="true">VS</span>${fighter(battle.opponent, false)}${hitEffects}</div>${odds}<div class="battle-prize" id="battle-prize">${legacy ? tr('battle.legacyPrize') : free ? rich('battle.freePrize', { amount: money(battle.wager.win_payout_minor) }) : rich('battle.paidPrize', { amount: money(battle.wager?.win_payout_minor) })}</div></section>
      <details class="help-card battle-help"><summary>${tr('battle.helpTitle')}</summary><p>${tr(battle.opponent.is_bot ? 'battle.oddsBot' : 'battle.oddsOnline', { taps: fmt(battle.tap_cap) })}</p><p>${tr('battle.helpBody')}</p></details>${finished}
    </div>
    <div class="battle-controls"><div class="tap-score"><span>${tr('battle.yourTaps')} <strong>${fmt(battle.you.taps)} / ${fmt(battle.tap_cap)}</strong></span><span>${tr('battle.opponentTaps')} <strong>${fmt(battle.opponent.taps)}</strong></span></div><button type="button" class="btn primary battle-tap" aria-keyshortcuts="Space" data-action="battle-tap" data-focus="battle-tap"${disabled(Boolean(pending && !busy) || maxed || now() >= battle.ends_at || preparing)}><strong id="battle-tap-title">${rich(maxed ? 'battle.tapMaxed' : 'battle.tapActive')}</strong><span>${maxed ? tr('battle.tapsAccepted') : tr('battle.tapHint', { taps: fmt(battle.tap_cap) })}</span></button><p class="battle-caption" id="battle-caption"></p></div>
  </section>`;
}

function renderResult(battle) {
  if (!battle.result) return '';
  const result = battle.result;
  const probability = Number.isFinite(battle.win_probability) ? `${fmt(Math.round(battle.win_probability * 100))}%` : '—';
  return `<section class="last-battle-card${result.won ? '' : ' loss'}" data-battle-id="${escapeHTML(battle.id)}" aria-label="${tr('result.title')}"><div class="last-battle-heading"><h2>${tr('result.title')}</h2><span class="last-battle-outcome">${tr(result.won ? 'common.win' : 'common.loss')}</span></div><div class="last-battle-summary"><span class="last-battle-opponent">${escapeHTML(fighterName(battle.opponent))} · ${tr(battle.opponent.is_bot ? 'common.botSuffix' : 'common.playerSuffix')}</span><strong class="last-battle-net${result.net_minor < 0 ? ' negative' : ''}">${rich('result.net', { amount: signedMoney(result.net_minor) })}</strong></div><p class="last-battle-details">${battle.wager ? `${tr('result.stake', { amount: money(battle.wager.stake_minor) })} · ` : ''}${tr('result.payout', { amount: money(result.payout_minor) })} · ${tr('result.xp', { amount: fmt(result.xp) })} · ${tr('result.chance', { probability })}${result.legacy ? ` · ${tr('result.legacyRules')}` : ''}</p></section>`;
}

function renderHistory() {
  const history = state.history || [];
  return `<div class="section-title"><h2>${tr('history.title')}</h2><small>${tr('history.stats', { wins: fmt(state.player.wins), losses: fmt(state.player.losses) })}</small></div>${history.length ? `<div class="history-list">${history.slice(0, 5).map((battle) => `<div class="history-row"><span class="history-mark${battle.result?.won ? '' : ' loss'}" aria-hidden="true">${icon(battle.result?.won ? 'check' : 'feather')}</span><div class="history-detail"><strong>${escapeHTML(fighterName(battle.opponent))}${battle.opponent.is_bot ? ` · ${tr('common.botSuffix')}` : ''}</strong><span>${tr(battle.result?.won ? 'common.win' : 'common.loss')} · ${escapeHTML(modeName(battle.mode))}</span></div><span class="history-reward${battle.result?.net_minor < 0 ? ' negative' : ''}">${signedMoney(battle.result?.net_minor)} ${coin()}</span></div>`).join('')}</div>` : `<div class="empty-state"><span aria-hidden="true">${icon('arena')}</span>${tr('history.emptyTitle')}<br>${tr('history.emptyBody')}</div>`}`;
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
  return `<p class="gear-feedback${gearFeedback.error ? ' is-error' : ''}" role="status">${escapeHTML(messageText(gearFeedback.message))}</p>`;
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
    <div class="gear-price">${cost !== null ? `<span>${tr('gear.cost')}</span><strong>${money(cost)} ${coin()}</strong>` : ''}</div>
    <button type="button" class="btn${primary ? ' primary' : ''}" data-action="${status === 'retry' ? 'gear-retry' : action}" ${attributes} data-focus="${escapeHTML(key)}" aria-describedby="${escapeHTML(key)}-status"${disabled(!['ready', 'retry'].includes(status))}>${tr(status === 'loading' ? 'gear.processing' : status === 'retry' ? 'common.retry' : label)}</button>
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
  return `<article class="upgrade-row" data-slot-id="${escapeHTML(slot.id)}" data-state="${status}" aria-busy="${status === 'loading'}">
    <div class="gear-item-heading"><div class="gear-item-art">${slotSVG(slot.id)}</div><div><h3>${escapeHTML(name)}</h3><p>${tr('common.level', { level: fmt(level) })}</p></div></div>
    <p class="gear-benefit">${tr('gear.currentBenefit', { power: fmt(level * slot.power_per_level) })}</p>
    ${capped ? `<p class="gear-complete">${tr('gear.maxLevel')}</p>` : `<p class="gear-next">${tr('gear.nextBenefit', { level: fmt(level + 1), power: fmt(slot.power_per_level) })}</p>${renderGearAction({ key, cost, status, action: 'upgrade', attributes: `data-slot="${escapeHTML(slot.id)}"`, label: 'gear.upgrade' })}`}
    ${renderGearFeedback(key)}
  </article>`;
}

function renderBreedChoice(breed) {
  const player = state.player;
  const equipped = player.breed_id === breed.id;
  const owned = player.owned_breeds.includes(breed.id);
  const key = `breed-${breed.id}`;
  const status = gearAvailability(key, owned ? null : breed.price_minor, equipped);
  const description = i18n.has(`breed.${breed.id}.description`) ? t(`breed.${breed.id}.description`) : breed.description;
  return `<article class="gear-breed${equipped ? ' is-equipped' : ''}" data-breed-id="${escapeHTML(breed.id)}" data-state="${status}" aria-busy="${status === 'loading'}">
    <div class="gear-breed-heading"><div class="gear-breed-art">${roosterSVG(`gear-${breed.id}`, breed.color)}</div><div><p class="gear-ownership">${rich(equipped ? 'gear.equipped' : owned ? 'gear.owned' : 'gear.notOwned')}</p><h3>${escapeHTML(breedName(breed.id))}</h3><p class="gear-multiplier">${tr('gear.multiplier', { multiplier: fmt(breed.power_multiplier) })}</p></div></div>
    <p class="gear-description">${escapeHTML(description)}</p>
    ${equipped ? '' : renderGearAction({ key, cost: owned ? null : breed.price_minor, status, action: 'breed', attributes: `data-breed="${escapeHTML(breed.id)}"`, label: owned ? 'gear.equip' : 'gear.buyEquip', primary: !owned })}
    ${renderGearFeedback(key)}
  </article>`;
}

function renderGear() {
  const player = state.player;
  const breed = state.catalog.breeds.find(item => item.id === player.breed_id);
  return `<section class="gear-screen" aria-labelledby="gear-title">
    <h1 id="gear-title">${tr('gear.screenTitle')}</h1>
    <div class="gear-fighter"><div class="gear-fighter-art">${roosterSVG('gear-current', breed?.color)}</div><div><p>${tr('gear.currentFighter')}</p><h2>${escapeHTML(breedName(player.breed_id))}</h2><p>${tr('common.level', { level: fmt(player.level) })} · <strong>${tr('arena.fighterPower', { power: fmt(player.power) })}</strong></p></div></div>
    ${lockedGear() ? `<p class="gear-lock" role="status">${tr('gear.locked')}</p>` : ''}
    ${blocked() && !lockedGear() ? `<p class="gear-lock" role="status">${tr(busy ? 'gear.commandPending' : 'gear.commandRetry')}</p>` : ''}
    <section aria-labelledby="gear-upgrades-title"><h2 id="gear-upgrades-title">${tr('gear.upgradesTitle')}</h2><p class="gear-section-note">${tr('gear.upgradesHint')}</p><div class="gear-upgrades equipment-grid">${state.catalog.slots.map(renderUpgradeRow).join('')}</div></section>
    <section class="gear-breeds" aria-labelledby="gear-breeds-title"><h2 id="gear-breeds-title">${tr('gear.breeds')}</h2><p class="gear-section-note">${tr('gear.intro')}</p><div class="gear-breed-list">${state.catalog.breeds.map(renderBreedChoice).join('')}</div></section>
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
  return `<p class="roost-feedback${feedback.error ? ' is-error' : ''}" role="status">${escapeHTML(messageText(message))}</p>`;
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
  return `<section class="roost-reward roost-${kind}" data-reward="${kind}" data-state="${status}" aria-busy="${status === 'claiming'}" aria-labelledby="roost-${kind}-title">
    <h2 id="roost-${kind}-title">${tr(daily ? 'roost.dailyTitle' : 'roost.passiveTitle')}</h2>
    <p class="roost-amount">${amountText} <span>${tr('roost.coins')}</span></p>
    <p class="roost-description" id="roost-${kind}-description">${tr(daily ? next ? 'roost.nextAvailable' : ownPending ? 'roost.confirming' : 'roost.readyNow' : 'roost.savingsHint')}${next ? ` <strong>${escapeHTML(formatDate(economy.next_daily_at))}</strong>` : ''}</p>
    <button type="button" class="btn${primary ? ' primary' : ''}${!active ? ' roost-unavailable' : ''}" data-action="${action}" data-focus="claim-${kind}" aria-describedby="roost-${kind}-description"${disabled(ownPending ? busy : blocked() || !available)}>${tr(label)}</button>
    ${ownPending && !busy ? `<p class="roost-description">${tr('roost.retrySafe')}</p>` : blocked() && !ownPending && available ? `<p class="roost-description">${tr('roost.waitingCommand')}</p>` : ''}
    ${renderRoostFeedback(kind)}
  </section>`;
}
function renderRoost() {
  const { player } = state;
  const breed = state.catalog.breeds.find(item => item.id === player.breed_id);
  const referralAvailable = Boolean(config.bot_username && !player.is_dev);
  return `<section class="roost-screen" aria-labelledby="roost-title">
    <header class="roost-heading"><div><h1 id="roost-title">${tr('roost.screenTitle')}</h1><p>${escapeHTML(breedName(player.breed_id))} · ${tr('common.level', { level: fmt(player.level) })}</p></div><div class="roost-portrait">${roosterSVG('roost-home', breed?.color)}</div></header>
    ${renderRoostReward('daily')}
    ${renderRoostReward('passive')}
    <section class="roost-progress" aria-labelledby="roost-progress-title"><h2 id="roost-progress-title">${tr('roost.progressTitle')}</h2><dl>
      <div><dt>${tr('roost.experience')}</dt><dd>${fmt(player.xp_in_level)} / ${fmt(player.xp_to_next)}</dd></div>
      <div><dt>${tr('roost.power')}</dt><dd>${fmt(player.power)}</dd></div>
      <div><dt>${tr('roost.battles')}</dt><dd>${fmt(player.battles)}</dd></div>
      <div><dt>${tr('roost.pvpWins')}</dt><dd>${fmt(player.pvp_wins)}</dd></div>
    </dl></section>
    <section class="roost-referral" aria-labelledby="roost-referral-title"><h2 id="roost-referral-title">${tr('roost.inviteTitle')}</h2><p>${tr('roost.referralBody')}</p>
      ${referralAvailable ? `<div class="roost-invite-actions"><button type="button" class="btn" data-action="invite" data-focus="roost-invite">${rich('roost.invite')}</button><button type="button" class="btn ghost" data-action="copy-invite" data-focus="roost-copy"${disabled(roostCopying)}>${tr(roostCopying ? 'roost.copying' : 'roost.copyInvite')}</button></div>${renderRoostFeedback('invite')}` : `<p class="roost-description">${tr(player.is_dev ? 'roost.telegramOnly' : 'roost.botSetup')}</p>`}
    </section><p class="roost-footnote">${tr('roost.playMoney')}</p>
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
        return `<li class="rankings-row${index < 3 ? ' is-leading' : ''}${own ? ' is-you' : ''}${fmt(player[ranking]).length > 9 ? ' has-long-value' : ''}"${own ? ' id="rankings-self" tabindex="-1"' : ''}><span class="rankings-rank">${fmt(index + 1)}</span><span class="leaderboard-name">${escapeHTML(player.name)}${own ? `<small>${tr('leaderboard.you')}</small>` : ''}</span><strong class="rankings-value"><span class="rankings-sr-only">${tr(metric)}: </span>${fmt(player[ranking])}</strong></li>`;
      }).join('')}</ol>`;
  } else if (leaderboardLoading) {
    content = `<div class="rankings-skeleton" aria-hidden="true">${Array.from({ length: 5 }, () => '<div><i></i><i></i><i></i></div>').join('')}</div>`;
  } else if (leaderboardError) {
    content = `<div class="rankings-empty"><h2>${tr('leaderboard.unavailable')}</h2><p>${tr('leaderboard.tryAgain')}</p></div>`;
  } else {
    content = `<div class="rankings-empty"><h2>${tr('leaderboard.emptyTitle')}</h2><p>${tr('leaderboard.empty')}</p></div>`;
  }
  // Positions come only from the server-ordered list. No rank is inferred outside it.
  const ownSummary = !hasResults ? '' : ownIndex >= 5
    ? `<button type="button" class="rankings-own" data-action="ranking-self" data-focus="ranking-self">${tr('leaderboard.yourPosition', { rank: fmt(ownIndex + 1) })}<span>${tr('leaderboard.showPosition')} ${icon('arrow-down')}</span></button>`
    : ownIndex < 0 ? `<p class="rankings-outside">${tr('leaderboard.outside')}</p>` : '';
  return `<section class="rankings-screen" aria-labelledby="rankings-title">
    <header class="rankings-heading"><h1 id="rankings-title">${tr('nav.leaderboard')}</h1><button type="button" class="rankings-refresh" data-action="refresh-leaderboard" data-focus="refresh-leaderboard" aria-label="${tr('leaderboard.refresh')}" title="${tr('leaderboard.refresh')}"${disabled(leaderboardLoading)}>${refreshIcon}</button></header>
    <div class="ranking-options" role="group" aria-label="${tr('a11y.ranking')}">${['power', 'pvp_wins'].map(mode => `<button type="button" data-action="ranking" data-ranking="${mode}" data-focus="ranking-${mode}" aria-pressed="${ranking === mode}">${icon('check')}${tr(mode === 'power' ? 'leaderboard.rankingPower' : 'leaderboard.rankingWins')}</button>`).join('')}</div>
    <p class="rankings-status${leaderboardError ? ' is-error' : ''}" role="status" aria-live="polite">${status}${leaderboardError ? ` <button type="button" data-action="refresh-leaderboard" data-focus="rankings-retry">${tr('common.retry')}</button>` : ''}</p>
    ${ownSummary}<div class="rankings-results" aria-busy="${leaderboardLoading}">${content}</div>
    <p class="rankings-note">${tr(state.player.is_dev ? 'leaderboard.devNote' : 'leaderboard.note')}</p>
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
    if (button) button.disabled = preparing || ended || maxed || Boolean(pending && !busy);
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
      : t(maxed ? 'battle.captionMaxed' : 'battle.captionCombat');
    feedback.battle(battle, preparing ? 'preparing' : ended ? 'ended' : 'active');
  }
  if (state.queue && $('#queue-time')) {
    const elapsed = Math.max(0, Math.floor(now() - state.queue.joined_at));
    $('#queue-time').textContent = t('queue.elapsed', { time: `${fmt(Math.floor(elapsed / 60))}:${String(elapsed % 60).padStart(2, '0')}` });
  }
}

function goTo(nextTab) {
  if (!['arena', 'gear', 'roost', 'leaderboard'].includes(nextTab)) return;
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
  if (action === 'close-result') { closeResultToast(); return; }
  if (action === 'retry' || action === 'gear-retry' || action === 'roost-retry') return sendPending();
  if (action === 'dismiss-notice') return clearNotice();
  if (action === 'boot-retry') return boot();
  if (!state) return;
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
    if (!blocked() && state.catalog.battle.stakes_minor.includes(value)) { selectedStake = value; storage.set('stakeMinor', String(value)); render(); }
    return;
  }
  if (action === 'arena-mode') {
    if (!blocked() && ['bot', 'online'].includes(button.dataset.mode)) {
      selectedMode = button.dataset.mode;
      storage.set('arenaMode', selectedMode);
      render();
    }
    return;
  }
  if (action === 'battle-tap') {
    if (!activeBattle() || state.battle.you.taps >= state.battle.tap_cap || now() >= state.battle.ends_at || now() < state.battle.starts_at || (pending && !busy)) return;
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

document.addEventListener('click', (event) => {
  const languageButton = event.target.closest('[data-language]');
  if (languageButton) return changeLanguage(languageButton.dataset.language);
  const tabButton = event.target.closest('[data-tab]');
  if (tabButton) return goTo(tabButton.dataset.tab);
  const button = event.target.closest('[data-action]');
  if (button && !button.disabled) handleAction(button.dataset.action, button).catch(() => showNotice({ key: 'notice.actionFailed' }, { error: true }));
});

/** Space uses the same combat path as a tap, including timing, cap and effects.
 * Own both key events so a focused button cannot add a native click on keyup.
 * Leave text entry and other controls with their normal keyboard behavior.
 */
document.addEventListener('keydown', (event) => {
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
  if (!booting) refreshState();
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
    main.innerHTML = `<section class="loading-view" aria-busy="true"><span class="loading-rooster" aria-hidden="true">${icon(bootFailure ? 'alert' : 'rooster')}</span><h1 data-i18n="loading.title">${tr('loading.title')}</h1><p data-i18n="loading.body">${tr('loading.body')}</p><div class="loading-bar"></div></section>`;
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
  } finally { booting = false; }
}

updateStaticLanguage();
boot();
