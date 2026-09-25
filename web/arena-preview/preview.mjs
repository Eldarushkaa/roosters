/** Isolated art-review page. Intentionally never imports app.js or an API client.
 * CSP connect-src 'none' independently prevents fetch/XHR/WebSocket/beacon commands.
 * All figures come from the explicit fixture, never production storage or auth.
 */
import {createI18n} from '../i18n.mjs';
import {createAppEnvironment} from '../environment.mjs';
import {icon as sharedIcon} from '../icons.mjs';
import {fixture as demo} from './fixture.mjs';

const params = new URLSearchParams(location.search);
const i18n = createI18n();
i18n.setLanguage(params.get('lang') === 'en' ? 'en' : 'ru');
const environment = createAppEnvironment();
const root = document.querySelector('#preview');
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tr = (key, args) => escape(i18n.t(key, args));
const text = (ru, en) => escape(i18n.getLanguage() === 'ru' ? ru : en);
const money = value => i18n.formatMoney(value);
const num = value => i18n.formatNumber(value);
const icon = name => sharedIcon(name, '', {classPrefix: 'ap-icon'});
const coin = () => icon('coin');
const stateNames = ['ready', 'loading', 'unavailable', 'noquote', 'error', 'empty', 'victory', 'first', 'long'];
const state = {mode: 'bot', stake: 5000, scene: stateNames.includes(params.get('state')) ? params.get('state') : 'ready', busy: false, notice: ''};
let timer;

function render() {
  const focus = document.activeElement?.id;
  const scrollTop = root.querySelector('.ap-scroll')?.scrollTop || 0;
  const rulesOpen = root.querySelector('#ap-rules')?.open || false;
  const bot = state.mode === 'bot';
  const loading = state.busy || state.scene === 'loading';
  const missing = state.scene === 'noquote' && bot;
  const balance = state.scene === 'unavailable' ? 500 : state.scene === 'long' ? 123456789010 : demo.balance;
  const insufficient = balance < state.stake;
  const first = state.scene === 'first' && bot;
  const unavailable = !first && (missing || insufficient);
  const disabled = loading ? ' disabled' : '';
  const payout = first ? money(1000) : missing ? '—' : bot ? demo.quotes[state.stake].map(money).join('–') : money(demo.onlineQuotes[state.stake]);
  const won = state.scene === 'victory';
  const noHistory = ['first', 'empty'].includes(state.scene);
  document.documentElement.lang = i18n.getLanguage();
  document.title = `${i18n.t('nav.arena')} · ${i18n.getLanguage() === 'ru' ? 'Предпросмотр' : 'Preview'}`;

  root.innerHTML = `<div class="ap-scroll" id="ap-scroll">
    <div class="ap-world">
      <header class="ap-header">
        <div class="ap-top">
          <div class="ap-brand"><span class="ap-brand-mark">${icon('rooster')}</span><div><strong>ROOSTER</strong><span>${tr('shell.brandSubtitle')}</span></div></div>
          <div class="ap-language ui-segmented ui-segmented-compact" role="group" aria-label="${tr('shell.language')}">${['ru','en'].map(lang => `<button id="ap-lang-${lang}" class="ui-button" type="button" data-lang="${lang}" aria-pressed="${lang === i18n.getLanguage()}">${lang.toUpperCase()}</button>`).join('')}</div>
        </div>
        <div class="ap-account"><div class="ap-player"><strong>${state.scene === 'long' ? text('ОченьДлинноеИмяБойцаБезПробелов', 'The undefeated champion of the yard') : tr('guest', {id: demo.guest})}</strong><span class="ap-connection"><i></i>${tr('shell.connected')}<span> · ${text('демо','demo')}</span></span></div><div class="ap-wallet" aria-label="${tr('shell.walletTitle')}">${coin()}<strong>${money(balance)}</strong><span>${tr('shell.coins')}</span></div></div>
      </header>
      <section class="ap-fighter" aria-label="${tr('shell.yourFighter')}">
        <div class="ap-fighter-info"><h1>${tr('breed.copper.name')}</h1><div class="ap-fighter-stats"><p><span>${tr('common.level', {level: num(demo.level)})}</span><span>${tr('arena.fighterPower', {power: num(demo.power)})}</span></p><div class="ap-xp"><div class="ap-xp-track ui-progress" role="progressbar" aria-label="${tr('a11y.xp')}" aria-valuemin="0" aria-valuemax="${demo.xpMax}" aria-valuenow="${demo.xp}"><span style="width:${demo.xp / demo.xpMax * 100}%"></span></div><span>${num(demo.xp)} / ${num(demo.xpMax)} XP</span></div></div></div>
        <img class="ap-hero" src="./assets/rooster-hero.webp" alt="${tr('a11y.rooster')}" width="1254" height="1254" fetchpriority="high">
      </section>
    </div>
    <main class="ap-main">
      <section class="ap-setup" aria-labelledby="ap-mode-title">
        <h2 class="ap-section-title ui-title ui-section-title" id="ap-mode-title">${tr('arena.modeTitle')}</h2>
        <div class="ap-modes ui-segmented" role="group" aria-labelledby="ap-mode-title">${['bot','online'].map(mode => `<button id="ap-mode-${mode}" type="button" class="ap-button ui-button ap-mode ap-mode-${mode}${mode === 'online' ? ' ui-accent' : ''}" data-mode="${mode}" aria-pressed="${state.mode === mode}"${disabled}>${icon(mode === 'bot' ? 'bot' : 'arena')}<span class="ap-mode-label ui-display">${tr(mode === 'bot' ? 'arena.botTitle' : 'arena.onlineTitle')}</span><span class="ap-selection ui-selection">${icon('check')}</span></button>`).join('')}</div>
        <div class="ap-stake-heading"><h2 class="ap-section-title ui-title ui-section-title" id="ap-stake-title">${tr('arena.stakeTitle')}</h2><span>${tr('arena.gameCoins')}</span></div>
        <div class="ap-stakes ui-chip-group" role="group" aria-labelledby="ap-stake-title">${demo.stakes.map(stake => `<button id="ap-stake-${stake}" type="button" class="ap-button ui-button ui-chip ap-stake" data-stake="${stake}" aria-pressed="${stake === state.stake}"${disabled}>${stake === state.stake ? icon('check') : ''}<span class="ui-display">${money(stake)}</span>${coin()}</button>`).join('')}</div>
        <div class="ap-risk ui-panel ui-panel-inset" aria-live="polite"><div><span>${tr('arena.lossLabel')}</span><strong>${first ? '0' : '−' + money(state.stake)} ${coin()}</strong></div><div><span>${tr('arena.winPayoutLabel')}</span><strong>${payout} ${coin()}</strong></div><p>${first ? tr('arena.firstFootnote') : tr(bot ? 'arena.botQuoteNote' : 'arena.onlineQuoteNote')}</p></div>
        ${unavailable ? `<p class="ap-status ui-status" data-tone="error" role="status">${tr(insufficient ? 'arena.insufficientFunds' : 'arena.quoteUnavailable')}</p>` : ''}
        <button id="ap-fight" class="ap-button ui-button ui-primary ui-display ap-fight" type="button" data-fight aria-busy="${loading}"${loading || unavailable ? ' disabled' : ''}>${loading ? `<span class="ap-loading ui-spinner" aria-hidden="true"></span>${tr('arena.processing')}` : first ? tr('arena.firstAction') : `${tr('arena.stakeLabel')} ${money(state.stake)} ${coin()}<span class="ap-fight-divider">·</span>${text(bot ? 'В бой' : 'Найти бой', bot ? 'Fight' : 'Find a fight')}${icon('arrow')}`}</button>
        <p class="ap-demo-note">${text('Предпросмотр · монеты не списываются', 'Preview · no coins are charged')}</p>
        <div id="ap-notice" class="ap-status ui-status" data-tone="${state.scene === 'error' ? 'error' : 'info'}" role="status" ${!state.notice && state.scene !== 'error' ? 'hidden' : ''}>${state.scene === 'error' ? text('Не удалось начать бой. Выбор сохранён.', 'Could not start the battle. Your selection is saved.') + `<button class="ap-retry ui-button" type="button" id="ap-retry" data-retry>${tr('common.retry')}</button>` : noticeText()}</div>
      </section>
      <section class="ap-result ui-result ${won ? 'ap-win' : 'ap-loss'}" data-tone="${won ? 'success' : 'error'}" aria-labelledby="ap-result-title"><div class="ap-result-heading"><h2 class="ui-title ui-result-heading" id="ap-result-title">${tr('result.title')}</h2>${noHistory ? '' : `<strong class="ap-outcome ui-outcome">${tr(won ? 'common.win' : 'common.loss')}</strong>`}</div>${noHistory ? `<p class="ap-empty">${tr('history.emptyTitle')} ${tr('history.emptyBody')}</p>` : `<div class="ap-result-body"><div class="ap-bot-portrait">${icon('bot')}</div><div class="ap-result-copy"><strong>${text('Клювдиатор · бот', 'Beakdiator · bot')}</strong><span class="ap-net ui-net">${text('Итог:', 'Net:')} ${won ? '+' + money(2656) : '−' + money(5000)} ${coin()}</span></div></div><p class="ap-result-details">${tr('result.stake', {amount: money(demo.result.stake)})} · ${tr('result.payout', {amount: money(won ? 7656 : demo.result.payout)})} · +${num(demo.result.xp)} XP · ${tr('result.chance', {probability: num(demo.result.chance) + '%'})}</p>`}</section>
      <section class="ap-support ui-info" aria-label="${text('На арене', 'In the arena')}">${icon('info')}<div><p class="ap-presence"><i></i>${tr('arena.presence', {count: num(0)})}</p><p>${tr(bot ? 'arena.botBody' : 'arena.onlineBody', {min: num(70), max: num(140)})}</p><p>${tr('arena.devPresence', {count: num(1)})}</p></div></section>
      <details class="ap-rules ui-panel ui-panel-raised" id="ap-rules"${rulesOpen ? ' open' : ''}><summary>${icon('document')}<span>${tr('arena.helpTitle')}</span><span class="ap-chevron" aria-hidden="true"></span></summary><div><p>${tr('arena.helpTaps', {seconds: num(10), taps: num(90)})}</p><p>${tr('arena.helpOnline')}</p></div></details>
      <section class="ap-history" aria-labelledby="ap-history-title"><div class="ap-history-heading">${icon('document')}<h2 class="ui-title" id="ap-history-title">${tr('history.title')}</h2></div><p>${tr('history.stats', {wins: num(noHistory ? 0 : demo.wins), losses: num(noHistory ? 0 : demo.losses)})}</p>${noHistory ? '' : `<div class="ap-history-row"><span>${text('Клювдиатор', 'Beakdiator')}<small>${tr(won ? 'common.win' : 'common.loss')} · ${tr('arena.botTitle')}</small></span><strong>${won ? '+' + money(2656) : '−' + money(5000)} ${coin()}</strong></div>`}</section>
    </main>
  </div>
  <nav class="ap-nav ui-nav" aria-label="${tr('shell.navigation')}">${[['arena','arena'],['gear','gear'],['roost','roost'],['leaderboard','trophy']].map(([tab, mark]) => `<button id="ap-tab-${tab}" type="button" class="ap-button ui-button ui-nav-item" data-tab="${tab}"${tab === 'arena' ? ' aria-current="page"' : ''}>${icon(mark)}<span>${tr('nav.' + tab)}</span></button>`).join('')}</nav>
  <div class="ap-toast" id="ap-nav-notice" role="status" hidden></div>`;
  root.querySelector('.ap-scroll').scrollTop = scrollTop;
  if (focus) document.getElementById(focus)?.focus({preventScroll: true});
}

function noticeText() {
  return state.notice === 'done' ? text('Демонстрация завершена. Баланс и результат не изменены.', 'Demo complete. Balance and result are unchanged.') : '';
}

root.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.lang) {
    i18n.setLanguage(button.dataset.lang);
    params.set('lang', button.dataset.lang);
    history.replaceState(null, '', '?' + params.toString());
  } else if (button.dataset.mode) state.mode = button.dataset.mode;
  else if (button.dataset.stake) state.stake = Number(button.dataset.stake);
  else if (button.hasAttribute('data-fight') || button.hasAttribute('data-retry')) {
    if (state.busy || state.scene === 'loading') return;
    state.scene = 'ready';
    state.notice = '';
    state.busy = true;
    clearTimeout(timer);
    timer = setTimeout(() => {state.busy = false; state.notice = 'done'; render();}, 1200);
  } else if (button.dataset.tab) {
    if (button.dataset.tab === 'arena') root.querySelector('.ap-scroll').scrollTo({top: 0, behavior: 'instant'});
    else {
      const toast = root.querySelector('#ap-nav-notice');
      toast.textContent = i18n.getLanguage() === 'ru' ? 'В предпросмотре доступна только Арена.' : 'Only Arena is available in this preview.';
      toast.hidden = false;
      setTimeout(() => {toast.hidden = true;}, 2500);
    }
    return;
  } else return;
  render();
});

render();
environment.start();
