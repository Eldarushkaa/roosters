import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {ART_LEVELS, artTier, roosterAppearance, roosterBattleFrame, renderRooster} from '../rooster-art.mjs';

test('all legal levels map to supported art and old snapshots have a safe appearance', () => {
  assert.deepEqual(Array.from({length: 11}, (_, n) => artTier(n)), [0,1,1,2,2,3,3,3,4,4,5]);
  assert.deepEqual(roosterAppearance({breed_id: 'storm'}), {breed: 'storm', helmet: 0, armor: 0, sword: 0});
  for (const level of [undefined, null, NaN, Infinity, '10', -2]) assert.equal(artTier(level), 0);
  assert.equal(artTier(11), 5);
  assert.equal(roosterAppearance({breed_id: '__proto__'}).breed, 'yard');
});

test('each purchase changes only its own rendered equipment part', () => {
  const frame = (markup, slot) => markup.match(new RegExp(`<svg class="rooster-part rooster-part-${slot}"[^]*?</svg>`))?.[0];
  for (const breed_id of ['yard', 'copper', 'storm', 'ember']) {
    const gear = {helmet: 3, armor: 3, sword: 3};
    const before = renderRooster({breed_id, gear});
    for (const slot of ['helmet', 'armor', 'sword']) {
      const after = renderRooster({breed_id, gear: {...gear, [slot]: 10}});
      assert.notEqual(frame(after, slot), frame(before, slot), `${breed_id} ${slot}`);
      for (const other of ['helmet', 'armor', 'sword'].filter(value => value !== slot)) {
        assert.equal(frame(after, other), frame(before, other), `${slot} affected ${other}`);
      }
    }
  }
});

test('renderer uses allowlisted local assets and escapes accessible labels', () => {
  const markup = renderRooster({breed_id: '"><script>bad</script>', gear: {helmet: 10, armor: 10, sword: 10}}, {label: '"<bird>&', className: '" onload="bad'});
  assert.ok(!markup.includes('<script>'));
  assert.ok(!markup.includes(' onload="bad'));
  assert.ok(markup.includes('aria-label="&quot;&lt;bird&gt;&amp;"'));
  assert.ok(markup.includes('data-breed="yard"'));
  for (const match of markup.matchAll(/href="\/static\/([^\"]+)"/g)) assert.ok(existsSync(new URL(`../${match[1]}`, import.meta.url)));
  assert.ok(!renderRooster({gear: {helmet: 0}}).includes('rooster-part-helmet'));
});

test('all generated sprite atlases are present and WebP encoded', () => {
  for (const file of ['yard', 'copper', 'storm', 'ember', 'helmets', 'swords']) {
    const data = readFileSync(new URL(`../assets/roosters/${file}.webp`, import.meta.url));
    assert.equal(data.toString('ascii', 0, 4), 'RIFF');
    assert.equal(data.toString('ascii', 8, 12), 'WEBP');
  }
  assert.deepEqual(ART_LEVELS, [0,1,3,5,8,10]);
});

test('battle pair shares one complete framing without equalizing different builds', () => {
  const small = {breed_id: 'yard', gear: {helmet: 0, armor: 0, sword: 0}};
  const large = {breed_id: 'ember', gear: {helmet: 10, armor: 10, sword: 10}};
  const frame = roosterBattleFrame([small, large]);
  assert.deepEqual(frame, roosterBattleFrame([large, small]));
  assert.ok(roosterBattleFrame([small, small])[3] < frame[3]);
  for (const fighter of [small, large]) {
    const art = renderRooster(fighter, {frame});
    assert.ok(art.includes(`viewBox="${frame.join(' ')}"`));
    assert.equal(art.match(/<g transform="([^"]+)"/)[1], renderRooster(fighter).match(/<g transform="([^"]+)"/)[1]);
  }
  const tall = {breed_id: 'storm', gear: {helmet: 10, armor: 10, sword: 10}};
  assert.ok(roosterBattleFrame([tall, large])[1] <= 13);
  assert.deepEqual(roosterBattleFrame([]), [0,0,640,640]);
});
