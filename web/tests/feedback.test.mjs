import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createFeedback } from '../feedback.mjs';
import { icon, iconNames, iconText } from '../icons.mjs';

function fixture({ reduced = false, hidden = false, native = {} } = {}) {
  const calls = [], animations = [], selectors = [], classes = new Set(), timers = new Map();
  const node = {
    classList: { add: x => classes.add(x), remove: (...xs) => xs.forEach(x => classes.delete(x)) },
    animate(frames, options) {
      const animation = { frames, options, cancelled: false, cancel() { this.cancelled = true; } };
      animations.push(animation);
      return animation;
    },
  };
  const doc = { hidden, querySelector: selector => { selectors.push(selector); return node; } };
  const telegram = { platform: 'ios', isActive: true, isVersionAtLeast: () => true,
    HapticFeedback: { impactOccurred: x => calls.push(['impact', x]), notificationOccurred: x => calls.push(['notification', x]) }, ...native };
  const win = { matchMedia: () => ({ matches: reduced }),
    setTimeout(fn) { const id = timers.size + 1; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id) };
  return { feedback: createFeedback({ telegram, doc, win }), calls, animations, classes, selectors, timers, telegram, doc };
}

test('each allowed icon exists once in the sprite; labels and interpolated data stay safe', () => {
  const sprite = readFileSync(new URL('../icons.svg', import.meta.url), 'utf8');
  assert.deepEqual([...sprite.matchAll(/<symbol id="([^"]+)"/g)].map(x => x[1]).sort(), [...iconNames].sort());
  assert.equal(icon('unknown'), '');
  assert.match(icon('coin', 'Coins < & "'), /aria-label="Coins &lt; &amp; &quot;"/);
  assert.match(icon('check'), /aria-hidden="true"/);
  const rich = iconText('{name}: {amount} ✦', { name: '<img>⚔✓', amount: '1,234.56' }, 'Coins');
  assert.match(rich, /^&lt;img&gt;⚔✓: 1,234.56 <svg/);
  assert.equal((rich.match(/<svg/g) || []).length, 1);
});

test('meaningful events use one native haptic; polls and retried responses stay quiet', () => {
  const { feedback: f, calls, selectors } = fixture();
  f.battle({ id: 'one' }, 'preparing');
  f.battle({ id: 'one' }, 'active');
  for (let n = 0; n < 90; n++) f.battle({ id: 'one' }, 'active');
  f.command('/gear/upgrade', 'upgrade');
  f.command('/gear/upgrade', 'upgrade');
  f.command('/breed/buy', 'equip');
  f.command('/claim/passive', 'reward');
  f.command('/battle/tap', 'tap');
  f.command('/queue/join', 'queue');
  f.command('/gear/upgrade', 'failed', true);
  f.command('/gear/upgrade', 'failed', true);
  f.result({ id: 'one', result: { won: true } }, true);
  f.result({ id: 'one', result: { won: true } }, true);
  f.result({ id: 'history', result: { won: false } }, false);
  f.result({ id: 'two', result: { won: false } }, true);
  assert.deepEqual(calls, [['impact','medium'], ['impact','medium'], ['impact','medium'], ['impact','medium'],
    ['notification','error'], ['notification','success'], ['notification','warning']]);
  assert.ok(selectors.includes('.roost-passive .roost-feedback'));
});

test('resuming an already active battle does not replay its start', () => {
  const { feedback: f, calls } = fixture();
  f.battle({ id: 'one' }, 'active');
  f.battle({ id: 'one' }, 'active');
  assert.equal(calls.length, 0);
});

test('unsupported, inactive and throwing Telegram bridges cannot break actions', () => {
  for (const native of [{ platform: 'unknown' }, { isVersionAtLeast: () => false }, { isActive: false },
    { HapticFeedback: undefined }, { HapticFeedback: { impactOccurred() { throw Error('unsupported'); } } }]) {
    const { feedback: f, calls } = fixture({ native });
    assert.doesNotThrow(() => f.command('/gear/upgrade', 'one'));
    assert.equal(calls.length, 0);
  }
  const hidden = fixture({ hidden: true });
  hidden.feedback.command('/claim/daily', 'one');
  assert.equal(hidden.calls.length, 0);
  assert.doesNotThrow(() => createFeedback({ telegram: null, win: {}, doc: { querySelector: () => null } }).command('/gear/upgrade', 'one'));
});

test('wallet accents use only a changed confirmed balance for the same player', () => {
  const { feedback: f, animations, classes, timers } = fixture();
  const p = { id: 'one', balance_minor: 25000 };
  f.balance(null, p);
  f.balance(p, p);
  f.balance(p, { id: 'two', balance_minor: 30000 });
  assert.equal(animations.length, 0);
  f.balance(p, { ...p, balance_minor: 30000 });
  assert.deepEqual([...classes], ['money-increase']);
  f.balance(p, { ...p, balance_minor: 20000 });
  assert.deepEqual([...classes], ['money-decrease']);
  assert.equal(animations[0].cancelled, true);
  assert.equal(animations[1].options.duration, 180);
  assert.deepEqual(Object.keys(animations[1].frames[0]), ['opacity']);
  [...timers.values()].forEach(fn => fn());
  assert.equal(classes.size, 0);
});

test('reduced motion preserves confirmed values and semantic cues without animation', () => {
  const { feedback: f, animations, classes } = fixture({ reduced: true });
  f.balance({ id: 'one', balance_minor: 100 }, { id: 'one', balance_minor: 200 });
  f.battle({ id: 'one' }, 'preparing');
  f.battle({ id: 'one' }, 'active');
  f.command('/claim/daily', 'one');
  f.result({ id: 'one', result: { won: true } }, true);
  assert.equal(animations.length, 0);
  assert.deepEqual([...classes], ['money-increase']);
});
