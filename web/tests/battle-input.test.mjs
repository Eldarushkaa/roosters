import assert from 'node:assert/strict';
import test from 'node:test';
import { createBattleInput } from '../battle-input.mjs';

function setup(pointerEvents = true) {
  const hits = [];
  const button = { disabled: false, focus() { this.focused = true; } };
  const input = createBattleInput({ pointerEvents, onTap: target => hits.push(target) });
  const event = overrides => ({
    target: { closest: () => button }, button: 0, pointerType: 'touch', detail: 1,
    preventDefault() { this.defaultPrevented = true; }, ...overrides,
  });
  return { hits, button, input, event };
}

test('each finger hits on contact, before release; compatibility clicks never duplicate it', () => {
  const { input, hits, event } = setup();
  const first = event({ pointerId: 1, isPrimary: true });
  input.pointerDown(first);
  assert.equal(first.defaultPrevented, true);
  assert.equal(hits.length, 1);
  input.pointerDown(event({ pointerId: 2, isPrimary: false }));
  assert.equal(hits.length, 2, 'overlapping fingers are independent contacts');
  input.click(event());
  input.click(event({ detail: 2 }));
  assert.equal(hits.length, 2);
});

test('mouse primary press retains focus and counts once; right/middle presses do nothing', () => {
  const { input, hits, button, event } = setup();
  input.pointerDown(event({ pointerType: 'mouse' }));
  input.click(event({ pointerType: 'mouse' }));
  input.pointerDown(event({ button: 1 }));
  input.pointerDown(event({ button: 2 }));
  assert.equal(hits.length, 1);
  assert.equal(button.focused, true);
});

test('keyboard and assistive activation work before and after touch, without a debounce window', () => {
  const { input, hits, event } = setup();
  input.click(event({ detail: 0 }));
  input.pointerDown(event());
  input.click(event({ detail: 0 }));
  assert.equal(hits.length, 3);
});

test('disabled, already handled and unrelated controls cannot strike', () => {
  const { input, hits, button, event } = setup();
  for (const method of ['pointerDown', 'click']) {
    input[method](event({ target: { closest: () => null } }));
    input[method](event({ defaultPrevented: true, detail: 0 }));
    button.disabled = true;
    input[method](event({ detail: 0 }));
    button.disabled = false;
  }
  // A contact during roulette cannot turn into a hit when released after start.
  input.click(event());
  assert.equal(hits.length, 0);
});

test('browsers without Pointer Events retain their click fallback', () => {
  const { input, hits, event } = setup(false);
  input.pointerDown(event());
  input.click(event());
  input.click(event({ detail: 0 }));
  assert.equal(hits.length, 2);
});
