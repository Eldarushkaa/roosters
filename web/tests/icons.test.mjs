import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {icon, iconNames} from '../icons.mjs';

test('legacy aliases resolve and all consumers use the shared 24px sprite', async () => {
  const sprite = await readFile(new URL('../icons.svg', import.meta.url), 'utf8');
  const ids = [...sprite.matchAll(/<symbol\s+id="([^"]+)"/g)].map(match => match[1]);
  for (const [, target] of sprite.matchAll(/<use\s+href="#([^"]+)"/g)) {
    assert.ok(ids.includes(target), `missing alias target: ${target}`);
  }
  for (const name of iconNames) {
    assert.ok(icon(name).includes(`/icons.svg#${name}"`));
    assert.match(icon(name), /viewBox="0 0 24 24"/);
  }
});

test('preview class hooks share the same 24px icon renderer', () => {
  const preview = icon('bot', '', {classPrefix: 'ap-icon'});
  assert.match(preview, /class="ap-icon ap-icon-bot"/);
  assert.match(preview, /viewBox="0 0 24 24"/);
  assert.match(icon('bot', '', {classPrefix: 'x" onload="x'}), /class="ui-icon ui-icon-bot"/);
});
