/** Guide lifecycle and accessibility contracts; browser checks own real layout. */
import assert from 'node:assert/strict';
import test from 'node:test';
import { createGuide, GUIDE_STORAGE_KEY } from '../guide.mjs';

function harness({ values = new Map(), storage, nativeInert = true } = {}) {
  const listeners = new Map();
  const doc = {
    activeElement: null,
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
    dispatch(type, event = {}) {
      const action = { prevented: false, stopped: false, shiftKey: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...event };
      for (const callback of listeners.get(type) || []) callback(action);
      return action;
    },
  };
  class Element {
    constructor({ dataset = {}, className = '', hidden = false } = {}) {
      Object.assign(this, { dataset, className, hidden, disabled: false, isConnected: true, children: [],
        parent: null, attributes: new Map(), scrollTop: 0, writes: 0, html: '' });
      if (nativeInert) this.inert = false;
      const names = new Set();
      this.classList = { add: name => names.add(name), remove: name => names.delete(name), contains: name => names.has(name) };
    }
    append(child) { this.children.push(child); child.parent = this; return child; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    matches(selector) {
      if (selector === '[hidden]') return this.hidden;
      if (selector === '[inert]') return Boolean(this.inert || this.attributes.has('inert'));
      if (selector === '[aria-hidden="true"]') return this.getAttribute('aria-hidden') === 'true';
      return false;
    }
    closest(selector) {
      for (let node = this; node; node = node.parent) {
        if (selector.split(',').some(part => node.matches(part.trim()))) return node;
      }
      return null;
    }
    contains(target) { return target === this || this.children.some(child => child.contains(target)); }
    descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
    querySelectorAll(selector) {
      const nodes = this.descendants();
      if (selector === '[data-action="close-guide"]') return nodes.filter(node => node.dataset.action === 'close-guide');
      if (selector === '[data-focus]') return nodes.filter(node => node.dataset.focus);
      if (selector === '[data-language]') return nodes.filter(node => node.dataset.language);
      if (selector === '.guide-content') return nodes.filter(node => node.className === 'guide-content');
      return nodes.filter(node => node.control && !node.disabled);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus() { doc.activeElement = this; doc.dispatch('focusin', { target: this }); }
    get innerHTML() { return this.html; }
    set innerHTML(value) {
      this.writes++;
      this.html = value;
      for (const node of this.descendants()) node.isConnected = false;
      this.children = [];
      if (!value) return;
      const button = dataset => {
        const node = new Element({ dataset });
        node.control = true;
        return node;
      };
      this.append(button({ action: 'close-guide', focus: 'guide-close' }));
      const content = this.append(new Element({ className: 'guide-content' }));
      content.append(button({ language: 'ru', focus: 'guide-language-ru' }));
      content.append(button({ language: 'en', focus: 'guide-language-en' }));
      this.append(button({ action: 'close-guide', focus: 'guide-done' }));
    }
  }
  const body = doc.body = new Element();
  const shell = body.append(new Element());
  const trigger = shell.append(new Element({ hidden: true }));
  const outside = shell.append(new Element());
  const nav = body.append(new Element());
  const fallback = body.append(new Element());
  const overlay = body.append(new Element({ hidden: true }));
  const dialog = overlay.append(new Element());
  const background = [shell, nav, fallback];
  let language = 'ru', renders = 0, writes = 0;
  const backing = storage || { getItem: key => values.get(key) ?? null,
    setItem(key, value) { writes++; values.set(key, value); } };
  const guide = createGuide({ overlay, dialog, trigger, background, storage: backing, doc,
    getContent() { renders++; return `guide content: ${language}`; },
    getLanguage: () => language, fallbackFocus: () => fallback });
  return { doc, guide, dialog, overlay, trigger, shell, nav, outside, fallback, body, values,
    setLanguage(value) { language = value; }, get renders() { return renders; }, get writes() { return writes; },
    show() { guide.sync({ available: true, autoAllowed: true }); },
    controls() { return dialog.querySelectorAll('[data-focus]'); },
  };
}

test('first eligible launch opens once and acknowledgement persists across reloads', () => {
  const env = harness();
  assert.equal(env.guide.open(), false, 'Manual access waits for caller eligibility');
  env.show();
  assert.equal(env.guide.isOpen(), true);
  assert.equal(env.overlay.hidden, false);
  assert.equal(env.trigger.hidden, false);
  assert.equal(env.doc.activeElement.dataset.focus, 'guide-close');
  assert.equal(env.values.has(GUIDE_STORAGE_KEY), false, 'Showing is not acknowledgement');
  env.guide.close();
  assert.equal(env.values.get(GUIDE_STORAGE_KEY), '1');
  assert.equal(env.guide.isOpen(), false);
  assert.equal(env.doc.activeElement, env.trigger);
  assert.equal(env.dialog.innerHTML, '');
  assert.equal(env.dialog.querySelectorAll('[data-language]').length, 0);
  env.show();
  assert.equal(env.guide.isOpen(), false);
  env.guide.close();
  assert.equal(env.writes, 1, 'Closing a closed guide does not write another acknowledgement');
  const reloaded = harness({ values: env.values });
  reloaded.show();
  assert.equal(reloaded.guide.isOpen(), false);
  assert.equal(reloaded.guide.open(), true, 'Seen guides remain manually available');
});

test('queue and pending states defer automatic presentation while permitting manual help', () => {
  const env = harness();
  env.guide.sync({ available: true, autoAllowed: false });
  assert.equal(env.guide.isOpen(), false);
  assert.equal(env.trigger.hidden, false);
  assert.equal(env.values.has(GUIDE_STORAGE_KEY), false);
  assert.equal(env.guide.open(), true);
  env.guide.sync({ available: true, autoAllowed: false });
  assert.equal(env.guide.isOpen(), true, 'A manual guide survives queue polling');
  env.guide.close();
  const deferred = harness();
  deferred.guide.sync({ available: true, autoAllowed: false });
  deferred.show();
  assert.equal(deferred.guide.isOpen(), true, 'Idle eligibility eventually shows the deferred introduction');
});

test('an active battle interrupts without marking seen and restores focus to a visible fallback', () => {
  const env = harness();
  env.show();
  env.guide.sync({ available: false, autoAllowed: false });
  assert.equal(env.trigger.hidden, true);
  assert.equal(env.guide.isOpen(), false);
  assert.equal(env.overlay.hidden, true);
  assert.equal(env.values.has(GUIDE_STORAGE_KEY), false);
  assert.equal(env.doc.activeElement, env.fallback);
  assert.equal(env.guide.open(), false);
  env.show();
  assert.equal(env.guide.isOpen(), true, 'The unfinished first guide can appear after battle/result dismissal');
});

test('polling preserves content nodes, focus and scroll; only a language change rebuilds content', () => {
  const env = harness();
  env.show();
  const content = env.dialog.querySelector('.guide-content');
  const selected = env.controls().find(node => node.dataset.language === 'en');
  selected.focus();
  content.scrollTop = 217;
  for (let index = 0; index < 5; index++) env.show();
  assert.equal(env.renders, 1);
  assert.equal(env.dialog.querySelector('.guide-content'), content);
  assert.equal(env.doc.activeElement, selected);
  assert.equal(content.scrollTop, 217);
  env.setLanguage('en');
  env.show();
  assert.equal(env.renders, 2);
  assert.notEqual(env.dialog.querySelector('.guide-content'), content);
  assert.equal(env.dialog.querySelector('.guide-content').scrollTop, 217);
  assert.equal(env.doc.activeElement.dataset.focus, 'guide-language-en');
  assert.notEqual(env.doc.activeElement, selected);
  assert.match(env.dialog.innerHTML, /content: en/);
});

test('storage errors retain seen state in memory and never prevent manual use', () => {
  const denied = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const env = harness({ storage: denied });
  assert.doesNotThrow(() => env.show());
  assert.doesNotThrow(() => env.guide.close());
  env.show();
  assert.equal(env.guide.isOpen(), false);
  assert.equal(env.guide.open(), true);
  env.guide.close();
  assert.equal(harness({ storage: denied }).guide.isOpen(), false);
});

test('background inert and aria-hidden values are restored exactly when dismissed', () => {
  const env = harness();
  env.nav.inert = true;
  env.nav.setAttribute('inert', 'inert');
  env.nav.setAttribute('aria-hidden', 'true');
  env.shell.setAttribute('aria-hidden', 'false');
  env.show();
  for (const node of [env.shell, env.nav, env.fallback]) {
    assert.equal(node.inert, true);
    assert.equal(node.getAttribute('aria-hidden'), 'true');
  }
  assert.equal(env.body.classList.contains('guide-open'), true);
  assert.equal(env.trigger.getAttribute('aria-expanded'), 'true');
  env.guide.close();
  assert.equal(env.shell.inert, false);
  assert.equal(env.shell.getAttribute('inert'), null);
  assert.equal(env.shell.getAttribute('aria-hidden'), 'false');
  assert.equal(env.nav.inert, true);
  assert.equal(env.nav.getAttribute('inert'), 'inert');
  assert.equal(env.nav.getAttribute('aria-hidden'), 'true');
  assert.equal(env.fallback.getAttribute('aria-hidden'), null);
  assert.equal(env.body.classList.contains('guide-open'), false);
  assert.equal(env.trigger.getAttribute('aria-expanded'), 'false');
});

test('keyboard wraps in both directions and Escape acknowledges the guide', () => {
  const env = harness();
  env.show();
  const [first, , , last] = env.controls();
  assert.equal(env.doc.dispatch('keydown', { key: 'Tab', shiftKey: true }).prevented, true);
  assert.equal(env.doc.activeElement, last);
  assert.equal(env.doc.dispatch('keydown', { key: 'Tab' }).prevented, true);
  assert.equal(env.doc.activeElement, first);
  assert.equal(env.doc.dispatch('keydown', { key: 'Tab' }).prevented, false, 'Native tab advances within the dialog');
  const escape = env.doc.dispatch('keydown', { key: 'Escape' });
  assert.equal(escape.prevented, true);
  assert.equal(escape.stopped, true);
  assert.equal(env.guide.isOpen(), false);
  assert.equal(env.values.get(GUIDE_STORAGE_KEY), '1');
});

test('older WebViews without native inert cannot move programmatic focus outside', () => {
  const env = harness({ nativeInert: false });
  env.show();
  const selected = env.controls()[2];
  selected.focus();
  env.outside.focus();
  assert.equal(env.doc.activeElement, selected);
  assert.equal('inert' in env.shell, false, 'Fallback does not leave an inert expando behind');
  env.guide.close();
  env.outside.focus();
  assert.equal(env.doc.activeElement, env.outside, 'Closing releases the focus guard');
  assert.equal(env.shell.getAttribute('inert'), null);
});

test('closing never restores focus to a hidden or disconnected trigger', () => {
  for (const unavailable of ['hidden', 'disconnected', 'hidden-parent']) {
    const env = harness();
    env.show();
    if (unavailable === 'hidden') env.trigger.hidden = true;
    if (unavailable === 'disconnected') env.trigger.isConnected = false;
    if (unavailable === 'hidden-parent') env.shell.hidden = true;
    env.guide.close();
    assert.equal(env.doc.activeElement, env.fallback, unavailable);
  }
});

test('a pre-existing body lock is preserved and closed keyboard events remain untouched', () => {
  const env = harness();
  env.body.classList.add('guide-open');
  env.show();
  env.guide.close();
  assert.equal(env.body.classList.contains('guide-open'), true);
  assert.equal(env.doc.dispatch('keydown', { key: 'Tab' }).prevented, false);
  assert.equal(env.doc.dispatch('keydown', { key: 'Escape' }).prevented, false);
});
