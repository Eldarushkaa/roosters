/** Small same-origin SVG sprite. Never substitutes symbols inside player data. */
export const iconNames = Object.freeze(['arena', 'gear', 'roost', 'trophy', 'power', 'coin', 'check', 'close', 'arrow-up-right', 'arrow-right', 'arrow-down', 'feather', 'rooster', 'refresh', 'alert']);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
export function icon(name, label = '') {
  if (!iconNames.includes(name)) return '';
  return `<svg class="ui-icon" data-icon="${name}" viewBox="0 0 24 24" focusable="false" ${label ? `role="img" aria-label="${escape(label)}"` : 'aria-hidden="true"'}><use href="/static/icons.svg#${name}"/></svg>`;
}
const glyphs = Object.freeze({'✦':'coin', 'ϟ':'power', '✓':'check', '⚔':'arena', '↗':'arrow-up-right', '→':'arrow-right'});
/** Translate the trusted template first; interpolate parameters only as text. */
export function iconText(template, params = {}, coinsLabel = '') {
  return template.split(/(\{[a-zA-Z][a-zA-Z0-9_]*\}|[✦ϟ✓⚔↗→])/g).map(part => {
    if (glyphs[part]) return icon(glyphs[part], part === '✦' ? coinsLabel : '');
    const parameter = /^\{([a-zA-Z][a-zA-Z0-9_]*)\}$/.exec(part);
    return escape(parameter && Object.hasOwn(params, parameter[1]) ? params[parameter[1]] : part);
  }).join('');
}
