/** Approved v2 characters. Atlas coordinates are presentation only; the server
 * supplies breed and each independently upgraded equipment level. */
export const ART_LEVELS = Object.freeze([0, 1, 3, 5, 8, 10]);
const ASSETS = '/static/assets/roosters/';
const ATLAS = {width: 1536, height: 1024};
const BODY_BOUNDS = {yard: [119, 9, 449, 511], copper: [40, 23, 507, 511], storm: [170, 8, 491, 504], ember: [37, 10, 481, 513]};

// Coordinates refer to one 512px body frame. Breed scale is deliberately not
// normalized: the frail Yard and broad Ember remain different sizes in combat.
const BREEDS = Object.freeze({
  yard: {scale: .69, head: [250, 64, 135, 139], hand: [390, 242], offsets: [[0,0],[-33,0],[-66,0],[0,1],[-32,1],[-64,1]]},
  copper: {scale: .81, head: [222, 69, 166, 169], hand: [462, 286], offsets: [[0,0],[-12,-1],[-13,-1],[0,-16],[-11,-16],[-13,-16]]},
  storm: {scale: 1, head: [299, 40, 99, 107], hand: [424, 202], offsets: [[0,0],[-66,0],[-118,0],[3,-1],[-69,0],[-122,-1]]},
  ember: {scale: 1, head: [204, 64, 162, 165], hand: [443, 244], offsets: [[0,0],[-3,0],[-6,-1],[0,-10],[-2,-10],[-6,-11]]},
});
// Transparent helmet frames are cropped to their material outline, keeping
// the eye/beak opening transparent. The comb belongs to the body beneath it.
const HELMETS = [null,
  [592, 109, 367, 360], [1093, 104, 381, 371],
  [76, 598, 375, 363], [590, 592, 391, 369], [1083, 553, 416, 411],
];
// source crop, point on the handle held by the closed fist, runtime scale.
const SWORDS = [
  {crop: [49, 542, 139, 414], grip: [118, 862], scale: .38},
  {crop: [269, 465, 141, 491], grip: [338, 849], scale: .39},
  {crop: [482, 351, 195, 605], grip: [577, 843], scale: .43},
  {crop: [753, 210, 179, 746], grip: [817, 826], scale: .43},
  {crop: [934, 93, 289, 863], grip: [1077, 807], scale: .40},
  {crop: [1232, 36, 286, 932], grip: [1375, 810], scale: .40},
];

const esc = value => String(value).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

export function artTier(level) {
  const value = Number.isFinite(level) ? Math.max(0, Math.min(10, Math.floor(level))) : 0;
  return ART_LEVELS.reduce((tier, threshold, index) => value >= threshold ? index : tier, 0);
}

export function roosterAppearance(fighter = {}) {
  const breed = Object.hasOwn(BREEDS, fighter?.breed_id) ? fighter.breed_id : 'yard';
  return {breed, helmet: artTier(fighter?.gear?.helmet), armor: artTier(fighter?.gear?.armor), sword: artTier(fighter?.gear?.sword)};
}

function layer(file, crop, box, slot, priority) {
  const [sx, sy, sw, sh] = crop;
  const [x, y, width, height] = box;
  return `<svg class="rooster-part rooster-part-${slot}" data-slot="${slot}" x="${x}" y="${y}" width="${width}" height="${height}" viewBox="${sx} ${sy} ${sw} ${sh}" preserveAspectRatio="none" overflow="hidden"><image class="rooster-layer" href="${ASSETS}${file}.webp" width="${ATLAS.width}" height="${ATLAS.height}"${priority ? ' fetchpriority="high"' : ''}/></svg>`;
}

/** Fit the complete pair, not each bird separately: a small opponent stays
 * small, while two small birds can use the available battle area clearly. */
export function roosterBattleFrame(fighters = []) {
  if (!fighters.length) return [0, 0, 640, 640];
  const bounds = fighters.map(fighter => {
    const look = roosterAppearance(fighter);
    const breed = BREEDS[look.breed];
    const weapon = SWORDS[look.sword];
    const [cx, cy, cw, ch] = weapon.crop;
    const wx = breed.hand[0] - (weapon.grip[0] - cx) * weapon.scale;
    const wy = breed.hand[1] - (weapon.grip[1] - cy) * weapon.scale;
    const [l, t, r, b] = BODY_BOUNDS[look.breed];
    const [hx, hy, hw, hh] = breed.head;
    const x = (640 - 512 * breed.scale) / 2 - 28;
    const y = 622 - 512 * breed.scale;
    return [x + Math.min(l, wx, look.helmet ? hx : l) * breed.scale,
      y + Math.min(t, wy, look.helmet ? hy : t) * breed.scale,
      x + Math.max(r, wx + cw * weapon.scale, look.helmet ? hx + hw : r) * breed.scale,
      y + Math.max(b, wy + ch * weapon.scale, look.helmet ? hy + hh : b) * breed.scale];
  });
  const left = Math.floor(Math.min(...bounds.map(b => b[0])) - 12);
  const top = Math.floor(Math.min(...bounds.map(b => b[1])) - 12);
  const right = Math.ceil(Math.max(...bounds.map(b => b[2])) + 12);
  const bottom = Math.ceil(Math.max(...bounds.map(b => b[3])) + 12);
  return [left, top, right - left, bottom - top];
}

export function renderRooster(fighter = {}, {label = '', className = '', priority = false, frame} = {}) {
  const look = roosterAppearance(fighter);
  const breed = BREEDS[look.breed];
  const sourceX = (look.armor % 3) * 512;
  const sourceY = Math.floor(look.armor / 3) * 512;
  const weapon = SWORDS[look.sword];
  const [dx, dy] = breed.offsets[look.armor];
  const [cx, cy, cw, ch] = weapon.crop;
  const swordX = breed.hand[0] - (weapon.grip[0] - cx) * weapon.scale;
  const swordY = breed.hand[1] - (weapon.grip[1] - cy) * weapon.scale;
  // A fixed 640px stage reserves space above the body for long blades. All
  // breeds stand on y=622, regardless of equipment or presentation context.
  const x = (640 - 512 * breed.scale) / 2 - 28;
  const y = 622 - 512 * breed.scale;
  const viewBox = Array.isArray(frame) && frame.length === 4 && frame.every(Number.isFinite) && frame[2] > 0 && frame[3] > 0 ? frame.join(' ') : '0 0 640 640';
  return `<svg xmlns="http://www.w3.org/2000/svg" class="rooster-art${className ? ` ${esc(className)}` : ''}" role="img" aria-label="${esc(label)}" focusable="false" viewBox="${viewBox}" preserveAspectRatio="xMidYMax meet" data-breed="${look.breed}" data-helmet-tier="${look.helmet}" data-armor-tier="${look.armor}" data-sword-tier="${look.sword}"><g transform="translate(${x} ${y}) scale(${breed.scale})">${layer('swords', weapon.crop, [swordX, swordY, cw * weapon.scale, ch * weapon.scale], 'sword', priority)}${layer(look.breed, [sourceX, sourceY, 512, 512], [-dx, -dy, 512, 512], 'armor', priority)}${look.helmet ? layer('helmets', HELMETS[look.helmet], breed.head, 'helmet', priority) : ''}</g></svg>`;
}
