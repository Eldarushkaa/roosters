/** Locale behavior and coverage contracts, without the browser or game state. */
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import test from 'node:test';
import { botNameKeys, createI18n, dictionaries, LANGUAGE_STORAGE_KEY, languages, messages } from '../i18n.mjs';

// Date expectations should not depend on the machine running this test file.
process.env.TZ = 'UTC';

function memoryStorage(initial = {}) {
  const saved = new Map(Object.entries(initial));
  return {
    saved,
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, String(value)),
  };
}

test('Russian is the default with absent storage and with an empty saved preference', () => {
  for (const storage of [undefined, null, memoryStorage()]) {
    const i18n = createI18n(storage);
    assert.equal(i18n.getLanguage(), 'ru');
    assert.equal(i18n.locale(), 'ru-RU');
    assert.equal(i18n.t('nav.arena'), 'Арена');
  }
  assert.deepEqual(languages, ['ru', 'en']);
});

test('the language choice survives a reload and affects only its own storage key', () => {
  const storage = memoryStorage({ 'rooster.v1.identity': 'existing player' });
  const i18n = createI18n(storage);
  assert.equal(i18n.setLanguage('en'), 'en');
  assert.equal(LANGUAGE_STORAGE_KEY, 'rooster.v1.language');
  assert.equal(storage.saved.get(LANGUAGE_STORAGE_KEY), 'en');
  assert.equal(storage.saved.get('rooster.v1.identity'), 'existing player');
  const reloaded = createI18n(storage);
  assert.equal(reloaded.getLanguage(), 'en');
  assert.equal(reloaded.locale(), 'en-US');
  assert.equal(reloaded.t('nav.arena'), 'Arena');
  reloaded.setLanguage('ru');
  assert.equal(createI18n(storage).getLanguage(), 'ru');
});

test('unsupported stored or requested language codes fall back to Russian', () => {
  for (const invalid of ['', 'de', 'EN', 'en-US', '__proto__', null, undefined, 1]) {
    const i18n = createI18n(memoryStorage({ [LANGUAGE_STORAGE_KEY]: invalid }));
    assert.equal(i18n.getLanguage(), 'ru', `stored code ${String(invalid)}`);
    i18n.setLanguage('en');
    assert.equal(i18n.setLanguage(invalid), 'ru', `requested code ${String(invalid)}`);
    assert.equal(i18n.t('nav.gear'), 'Снаряжение');
  }
});

test('denied storage keeps a working in-memory language choice', () => {
  const denied = {
    getItem() { throw new Error('Storage denied'); },
    setItem() { throw new Error('Storage denied'); },
  };
  const i18n = createI18n(denied);
  assert.equal(i18n.getLanguage(), 'ru');
  assert.doesNotThrow(() => i18n.setLanguage('en'));
  assert.equal(i18n.getLanguage(), 'en');
  assert.equal(i18n.t('common.retry'), 'Retry');
  assert.equal(createI18n(denied).getLanguage(), 'ru', 'Denied writes cannot persist after reload');

  const readableOnly = createI18n({
    getItem: () => 'en',
    setItem() { throw new Error('Quota exceeded'); },
  });
  assert.equal(readableOnly.getLanguage(), 'en');
  readableOnly.setLanguage('ru');
  assert.equal(readableOnly.t('common.retry'), 'Повторить');
});

test('both catalogs have nonempty matching keys and interpolation parameters', () => {
  assert.equal(dictionaries, messages);
  const ruKeys = Object.keys(messages.ru).sort();
  assert.ok(ruKeys.length > 200, 'The complete screen catalog should be exported');
  assert.deepEqual(Object.keys(messages.en).sort(), ruKeys);
  const placeholders = (text) => [...text.matchAll(/\{([a-zA-Z][a-zA-Z0-9_]*)\}/g)].map((match) => match[1]).sort();
  for (const key of ruKeys) {
    assert.equal(typeof messages.ru[key], 'string', key);
    assert.equal(typeof messages.en[key], 'string', key);
    assert.ok(messages.ru[key].trim(), `${key} has Russian text`);
    assert.ok(messages.en[key].trim(), `${key} has English text`);
    assert.deepEqual(placeholders(messages.en[key]), placeholders(messages.ru[key]), key);
    assert.doesNotMatch(messages.en[key], /[\u0400-\u04ff]/u, `${key} should have no untranslated Russian`);
  }
});

test('explicit application and static HTML translation keys exist in the catalog', async () => {
  const [app, html] = await Promise.all([
    readFile(new URL('../app.js', import.meta.url), 'utf8'),
    readFile(new URL('../index.html', import.meta.url), 'utf8'),
  ]);
  const keys = new Set([
    ...[...app.matchAll(/\b(?:t|tr|localizedError)\('([^']+)'/g)].map((match) => match[1]),
    ...[...app.matchAll(/(?:key|messageKey):\s*'([^']+)'/g)].map((match) => match[1]),
    ...[...html.matchAll(/data-i18n(?:-aria|-title|-content)?="([^"]+)"/g)].map((match) => match[1]),
  ]);
  assert.ok(keys.size > 100);
  for (const key of keys) assert.ok(Object.hasOwn(messages.en, key), `Unknown explicit key: ${key}`);
});

test('every current backend error code has a specific English message', async () => {
  const directory = new URL('../../roosters/', import.meta.url);
  const files = (await readdir(directory)).filter((name) => name.endsWith('.py'));
  const source = (await Promise.all(files.map((name) => readFile(new URL(name, directory), 'utf8')))).join('\n');
  const knownCodes = new Set([
    ...[...source.matchAll(/GameError\(\s*["']([^"']+)["']/g)].map((match) => match[1]),
    ...[...source.matchAll(/"code"\s*:\s*"([^"+]+)"/g)].map((match) => match[1]).filter((code) => !code.startsWith('http_')),
  ]);
  assert.ok(knownCodes.size >= 30, 'Include service, auth, storage and internal failures');
  assert.ok(knownCodes.has('storage_unavailable'));
  assert.ok(knownCodes.has('internal_error'));
  const i18n = createI18n(memoryStorage({ [LANGUAGE_STORAGE_KEY]: 'en' }));
  for (const code of knownCodes) {
    const key = i18n.errorKey(code);
    assert.notEqual(key, 'error.server', `Specific message missing: ${code}`);
    assert.ok(i18n.has(key), code);
    assert.doesNotMatch(i18n.t(key), /[\u0400-\u04ff]/u, code);
  }
});

test('HTTP and unknown error codes use the safe generic message', () => {
  const i18n = createI18n();
  i18n.setLanguage('en');
  for (const code of ['http_400', 'http_403', 'http_404', 'http_500', 'new_server_error', '__proto__', 'constructor', 'toString', '', null, undefined]) {
    assert.equal(i18n.errorKey(code), 'error.server', String(code));
    assert.equal(i18n.t(i18n.errorKey(code)), 'The server could not complete the request. Please try again.');
  }
});

test('numbers, minor-unit money and dates follow the current language', () => {
  const i18n = createI18n();
  const timestamp = Date.UTC(2026, 8, 23, 12, 34) / 1000;
  assert.equal(i18n.formatNumber(12345), '12\u00a0345');
  assert.equal(i18n.formatMoney(123456), '1\u00a0234,56');
  assert.equal(i18n.formatMoney(-105), '-1,05');
  assert.equal(i18n.formatDate(timestamp), '23 сент., 12:34');
  i18n.setLanguage('en');
  assert.equal(i18n.formatNumber(12345), '12,345');
  assert.equal(i18n.formatMoney(123456), '1,234.56');
  assert.equal(i18n.formatMoney(-105), '-1.05');
  assert.equal(i18n.formatMoney(100), '1');
  assert.equal(i18n.formatDate(timestamp), 'Sep 23, 12:34 PM');
  for (const invalid of [undefined, null, '', 'unavailable', NaN, Infinity]) {
    assert.equal(i18n.formatNumber(invalid), '0');
    assert.equal(i18n.formatMoney(invalid), '0');
    assert.equal(i18n.formatDate(invalid), 'later');
  }
  i18n.setLanguage('ru');
  assert.equal(i18n.formatDate(0), 'позже');
});

test('interpolation preserves arbitrary names verbatim and leaves escaping to renderers', () => {
  const i18n = createI18n();
  const name = '<img src=x onerror="alert(1)"> & Иван {taps} $&';
  for (const language of ['ru', 'en']) {
    i18n.setLanguage(language);
    const output = i18n.t('notice.retrySafe', { message: name });
    assert.ok(output.startsWith(`${name} `), 'Do not escape, translate or recursively substitute supplied names');
    assert.equal(i18n.t('common.level', { level: 42 }), language === 'ru' ? 'Уровень 42' : 'Level 42');
    assert.equal(i18n.t('common.level'), messages[language]['common.level']);
    assert.equal(i18n.t('common.level', Object.create({ level: 'inherited' })), messages[language]['common.level']);
    assert.equal(i18n.t('guest', { id: 'Клювдиатор' }), language === 'ru' ? 'Гость Клювдиатор' : 'Guest Клювдиатор');
  }
  assert.equal(i18n.has('__proto__'), false);
  assert.equal(i18n.has('not.a.translation'), false);
  assert.equal(i18n.t('not.a.translation'), 'not.a.translation');
});

test('breed and gear translations are keyed by the stable server catalog IDs', async () => {
  const rules = await readFile(new URL('../../roosters/rules.py', import.meta.url), 'utf8');
  const ids = (section) => [...section.matchAll(/"id":\s*"([a-z_]+)"/g)].map((match) => match[1]);
  const breeds = ids(rules.match(/BREEDS = \[([\s\S]*?)\n\]/)[1]);
  const slots = ids(rules.match(/SLOTS = \[([\s\S]*?)\n\]/)[1]);
  assert.deepEqual(breeds, ['yard', 'copper', 'storm', 'ember']);
  assert.deepEqual(slots, ['helmet', 'armor', 'sword']);
  const i18n = createI18n();
  for (const id of breeds) {
    assert.ok(i18n.has(`breed.${id}.name`), id);
    assert.ok(i18n.has(`breed.${id}.description`), id);
  }
  for (const id of slots) assert.ok(i18n.has(`slot.${id}`), id);
  i18n.setLanguage('en');
  assert.equal(i18n.t('breed.yard.name'), 'Yard');
  assert.equal(i18n.t('slot.helmet'), 'Helmet');
});

test('known bot names have explicit keys, while plain text is never auto-translated', () => {
  const names = ['Клювдиатор', 'Сэр Кукарек', 'Полковник Зерно', 'Пернатый Джо'];
  assert.deepEqual(Object.keys(botNameKeys), names);
  const i18n = createI18n();
  for (const name of names) {
    const key = botNameKeys[name];
    assert.ok(i18n.has(key));
    i18n.setLanguage('ru');
    assert.equal(i18n.t(key), name);
    i18n.setLanguage('en');
    assert.doesNotMatch(i18n.t(key), /[\u0400-\u04ff]/u);
    assert.equal(i18n.t(name), name, 'Only an explicit bot lookup translates a bot identity');
  }
});
