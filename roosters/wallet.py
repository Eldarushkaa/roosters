"""Sole writer of the current wallet, in the caller's transaction.

One coin is 100 integer minor units. Legacy ``coins``, ``medals`` and ``ledger``
are immutable migration archives; no current operation writes those columns.
"""
from .errors import GameError


MAX_MINOR_UNITS = 2**63 - 1


def change(db, player_id, delta_minor, reason, reference, now):
    """Apply a signed amount exactly once per player/reason/reference.

    The owning use case opens one transaction for its state and wallet changes.
    Reusing a journal reference with a changed amount fails instead of silently
    crediting or charging twice. Zero amounts are also recorded, so even a zero
    migration opening has an explicit receipt and cannot later change value.
    """
    if type(delta_minor) is not int or not -MAX_MINOR_UNITS <= delta_minor <= MAX_MINOR_UNITS:
        raise ValueError("Invalid wallet operation")
    if not isinstance(reason, str) or not reason or not isinstance(reference, str) or not reference:
        raise ValueError("Wallet reason and reference are required")
    if not db.in_transaction:
        raise RuntimeError("Wallet operations require an active transaction")
    prior = db.execute("SELECT delta_minor FROM coin_ledger WHERE player_id=? AND reason=? AND reference=?",
                       (player_id, reason, reference)).fetchone()
    if prior:
        if prior["delta_minor"] != delta_minor:
            raise RuntimeError("Ledger reference reused with a different amount")
        return
    row = db.execute("SELECT balance_minor FROM players WHERE id=?", (player_id,)).fetchone()
    if not row:
        raise GameError("player_not_found", "Игрок не найден.", 404)
    balance = row["balance_minor"] + delta_minor
    if balance < 0:
        raise GameError("insufficient_funds", "Недостаточно монет.", 409)
    if balance > MAX_MINOR_UNITS:
        raise ValueError("Wallet balance exceeds the supported integer range")
    db.execute("UPDATE players SET balance_minor=? WHERE id=?", (balance, player_id))
    db.execute("""INSERT INTO coin_ledger(player_id,delta_minor,balance_after_minor,reason,reference,created_at)
                  VALUES (?,?,?,?,?,?)""", (player_id, delta_minor, balance, reason, reference, now))
