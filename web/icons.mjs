/** Shared 24px game sprite. Never substitutes symbols inside player data.
 * `arena` and `arrow` remain aliases for existing app and preview callers.
 */
export const iconNames = Object.freeze(['arena', 'swords', 'gear', 'roost', 'trophy', 'bot', 'power', 'coin', 'check', 'close', 'arrow-up-right', 'arrow', 'arrow-right', 'arrow-down', 'feather', 'rooster', 'refresh', 'alert', 'search', 'info', 'document']);
const spriteHref = new URL('./icons.svg', import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
/** Omit label beside live text; provide a localized label for meaningful standalone marks.
 * The preview opts into its existing sizing hooks with {classPrefix: 'ap-icon'}.
 */
export function icon(name, label = '', {classPrefix = 'ui-icon'} = {}) {
  if (!iconNames.includes(name)) return '';
  const prefix = /^[a-z][a-z0-9-]*$/i.test(classPrefix) ? classPrefix : 'ui-icon';
  return `<svg class="${prefix} ${prefix}-${name}" data-icon="${name}" viewBox="0 0 24 24" focusable="false" ${label ? `role="img" aria-label="${escape(label)}"` : 'aria-hidden="true"'}><use href="${escape(spriteHref)}#${name}"/></svg>`;
}
const glyphs = Object.freeze({'✦':'coin', 'ϟ':'power', '✓':'check', '⚔':'arena', '↗':'arrow-up-right', '→':'arrow-right', '⌕':'search'});
/** Translate the trusted template first; interpolate parameters only as text. */
export function iconText(template, params = {}, coinsLabel = '') {
  return template.split(/(\{[a-zA-Z][a-zA-Z0-9_]*\}|[✦ϟ✓⚔↗→⌕])/g).map(part => {
    if (glyphs[part]) return icon(glyphs[part], part === '✦' ? coinsLabel : '');
    const parameter = /^\{([a-zA-Z][a-zA-Z0-9_]*)\}$/.exec(part);
    return escape(parameter && Object.prototype.hasOwnProperty.call(params, parameter[1]) ? params[parameter[1]] : part);
  }).join('');
}
