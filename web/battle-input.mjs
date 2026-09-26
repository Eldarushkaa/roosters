/** Immediate combat input, independent of the browser's click/gesture decision.
 * Each contact counts once, including non-primary fingers. Movement, release
 * and a compatibility click never add hits. Keyboard/assistive clicks (detail=0)
 * and browsers without Pointer Events retain native button activation.
 * onTap still owns server timing/cap checks; this adapter changes no game state.
 */
export function createBattleInput({ onTap, pointerEvents = true }) {
  const control = event => event.target?.closest?.('[data-action="battle-tap"]');
  return {
    pointerDown(event) {
      const button = control(event);
      if (!pointerEvents || !button || button.disabled || event.defaultPrevented || event.button !== 0) return;
      event.preventDefault();
      // preventDefault suppresses native mouse focus along with selection.
      if (event.pointerType === 'mouse') button.focus({ preventScroll: true });
      onTap(button);
    },
    click(event) {
      const button = control(event);
      if (!button || button.disabled || event.defaultPrevented) return;
      // Pointer Events can still emit click after a cancelled pointerdown.
      // No time-based debounce: fast alternating fingers must all count.
      if (pointerEvents && event.detail > 0) return;
      onTap(button);
    },
  };
}
