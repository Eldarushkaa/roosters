/** Local guide presentation only; the caller owns eligibility and content. */
export const GUIDE_STORAGE_KEY = 'rooster.v1.guideSeen.v1';

export function createGuide({ overlay, dialog, trigger, background = [], storage,
  getContent, getLanguage, fallbackFocus, doc = document }) {
  let available = false, opened = false, seen = false, language = null;
  let previousBackground = [], hadBodyClass = false, lastFocus = null, redirecting = false;
  try { seen = storage?.getItem(GUIDE_STORAGE_KEY) === '1'; } catch { /* Document-local fallback below. */ }

  const visible = node => Boolean(node?.isConnected && !node.hidden && !node.disabled
    && !node.closest?.('[hidden], [inert], [aria-hidden="true"]')
    && (!node.getClientRects || node.getClientRects().length));
  const controls = () => [...dialog.querySelectorAll(
    'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )].filter(visible);

  function focus(node) {
    if (!visible(node)) return;
    try { node.focus({ preventScroll: true }); } catch { node.focus(); }
  }

  function focusStart() {
    const close = [...dialog.querySelectorAll('[data-action="close-guide"]')].find(visible);
    focus(close || controls()[0] || dialog);
  }

  function lockBackground() {
    previousBackground = background.filter(Boolean).map(node => ({ node,
      ariaHidden: node.getAttribute('aria-hidden'), inertAttribute: node.getAttribute('inert'),
      hasInert: 'inert' in node, inert: node.inert,
    }));
    for (const { node, hasInert } of previousBackground) {
      if (hasInert) node.inert = true;
      node.setAttribute('inert', '');
      node.setAttribute('aria-hidden', 'true');
    }
  }

  function unlockBackground() {
    for (const previous of previousBackground) {
      const { node } = previous;
      if (previous.hasInert) node.inert = previous.inert;
      for (const [attribute, value] of [['inert', previous.inertAttribute], ['aria-hidden', previous.ariaHidden]]) {
        if (value === null) node.removeAttribute(attribute);
        else node.setAttribute(attribute, value);
      }
    }
    previousBackground = [];
  }

  function renderContent() {
    const content = dialog.querySelector('.guide-content');
    const scrollTop = content?.scrollTop || 0;
    const active = dialog.contains(doc.activeElement) ? doc.activeElement : null;
    const focusKey = active?.dataset?.focus;
    const focusLanguage = active?.dataset?.language;
    const focusedDialog = active === dialog;
    dialog.innerHTML = getContent();
    language = getLanguage();
    if (opened) {
      const replacement = focusKey
        ? [...dialog.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === focusKey)
        : focusLanguage
          ? [...dialog.querySelectorAll('[data-language]')].find(node => node.dataset.language === focusLanguage)
          : null;
      if (replacement) focus(replacement);
      else if (focusedDialog) focus(dialog);
      else focusStart();
      // Restore after focus, including old WebViews without preventScroll.
      const nextContent = dialog.querySelector('.guide-content');
      if (nextContent) nextContent.scrollTop = scrollTop;
    }
  }

  function open() {
    if (!available) return false;
    if (opened) return true;
    renderContent();
    opened = true;
    overlay.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    hadBodyClass = Boolean(doc.body?.classList.contains('guide-open'));
    doc.body?.classList.add('guide-open');
    // Move focus before hiding its previous background from accessibility.
    focusStart();
    lockBackground();
    return true;
  }

  function dismiss(markSeen) {
    if (!opened) return;
    opened = false;
    if (markSeen) {
      seen = true;
      try { storage?.setItem(GUIDE_STORAGE_KEY, '1'); } catch { /* Seen for this document even if persistence is denied. */ }
    }
    overlay.hidden = true;
    trigger.setAttribute('aria-expanded', 'false');
    // Closed guide language controls must not duplicate the shell controls.
    dialog.innerHTML = '';
    lastFocus = null;
    unlockBackground();
    if (!hadBodyClass) doc.body?.classList.remove('guide-open');
    focus(visible(trigger) ? trigger : fallbackFocus?.());
  }

  function close() { dismiss(true); }

  function sync({ available: nextAvailable, autoAllowed }) {
    available = Boolean(nextAvailable);
    trigger.hidden = !available;
    if (!available) {
      // A match or sign-out can interrupt the guide without acknowledging it.
      dismiss(false);
      return;
    }
    if (opened) {
      if (language !== getLanguage()) renderContent();
    } else if (!seen && autoAllowed) open();
  }

  doc.addEventListener('keydown', event => {
    if (!opened) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = controls();
    const index = items.indexOf(doc.activeElement);
    if (!items.length || (event.shiftKey ? index <= 0 : index < 0 || index === items.length - 1)) {
      event.preventDefault();
      focus(items.length ? items[event.shiftKey ? items.length - 1 : 0] : dialog);
    }
  }, true);

  // Native inert is unavailable in older WebViews; contain programmatic focus too.
  doc.addEventListener('focusin', event => {
    if (!opened || redirecting) return;
    if (dialog.contains(event.target)) { lastFocus = event.target; return; }
    redirecting = true;
    try {
      if (dialog.contains(lastFocus) && visible(lastFocus)) focus(lastFocus);
      else focusStart();
    } finally { redirecting = false; }
  }, true);

  return { sync, open, close, isOpen: () => opened };
}
