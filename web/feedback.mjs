/** Cosmetic feedback only: never computes gameplay, money, or accepted input. */
export function createFeedback({ telegram, win = window, doc = document }) {
  const played = new Set();
  const animations = new WeakMap();
  let battleId = null, phase = null, balanceTimer;
  const visible = () => !doc.hidden && telegram?.isActive !== false;
  function haptic(kind, key) {
    if (!telegram || !visible() || played.has(key)) return;
    played.add(key);
    if (played.size > 64) played.delete(played.values().next().value);
    try {
      if (telegram.isVersionAtLeast && !telegram.isVersionAtLeast('6.1')) return;
      if (telegram.platform === 'unknown' && !telegram.initData) return;
      const h = telegram.HapticFeedback;
      if (kind === 'impact') h?.impactOccurred?.('medium');
      else h?.notificationOccurred?.(kind);
    } catch { /* An absent, partial or throwing native bridge cannot block play. */ }
  }
  function pulse(element, scale = false) {
    if (!element?.animate || !visible() || win.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) return;
    animations.get(element)?.cancel();
    const frames = scale ? [{transform:'scale(.985)', opacity:.8}, {transform:'scale(1)', opacity:1}]
      : [{opacity:.55}, {opacity:1}];
    animations.set(element, element.animate(frames, {duration:180, easing:'ease-out'}));
  }
  function balance(previous, next) {
    if (!previous || previous.id !== next.id || previous.balance_minor === next.balance_minor) return;
    const node = doc.querySelector('#balance');
    if (!node) return;
    win.clearTimeout?.(balanceTimer);
    node.classList.remove('money-increase', 'money-decrease');
    node.classList.add(next.balance_minor > previous.balance_minor ? 'money-increase' : 'money-decrease');
    pulse(node);
    balanceTimer = win.setTimeout?.(() => node.classList.remove('money-increase', 'money-decrease'), 700);
  }
  function battle(current, nextPhase) {
    if (current.id === battleId && phase === 'preparing' && nextPhase === 'active') {
      haptic('impact', `battle:${current.id}`);
      pulse(doc.querySelector('.battle-tap'), true);
      pulse(doc.querySelector('#battle-phase'));
    }
    battleId = current.id;
    phase = nextPhase;
  }
  function command(path, key, error = false) {
    if (error) { haptic('error', `error:${key}`); return; }
    if (['/gear/upgrade', '/breed/buy', '/claim/daily', '/claim/passive'].includes(path)) {
      haptic('impact', `command:${key}`);
      pulse(doc.querySelector(path.startsWith('/claim/') ? '.roost-feedback' : '.gear-feedback'));
    }
  }
  function result(battle, live) {
    pulse(doc.querySelector('#battle-result-toast'));
    if (live) haptic(battle.result.won ? 'success' : 'warning', `result:${battle.id}`);
  }
  return { balance, battle, command, result };
}
