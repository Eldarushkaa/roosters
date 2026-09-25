"""Integer wallet guarantees against disposable SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from roosters.errors import GameError
from roosters.storage import Database
from roosters import wallet


class WalletTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="roosters-wallet-")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "game.sqlite3")
        self.database.migrate()
        with self.database.transaction() as db:
            db.execute("""INSERT INTO players(id,name,is_dev,coins,medals,energy_at,passive_at,last_seen,created_at,referral_code)
                          VALUES ('tg:1','Player',0,123,7,1,1,1,1,'ref_player')""")
            wallet.change(db, "tg:1", 1000, "welcome", "tg:1", 1)

    def state(self):
        with closing(self.database.connect()) as db:
            player = dict(db.execute("SELECT balance_minor,coins,medals FROM players WHERE id='tg:1'").fetchone())
            entries = [dict(row) for row in db.execute("SELECT * FROM coin_ledger ORDER BY id")]
            return player, entries

    def change(self, amount, reason="battle", reference="battle-1"):
        with self.database.transaction() as db:
            wallet.change(db, "tg:1", amount, reason, reference, 2)

    def test_identical_replay_does_not_charge_again_or_touch_archived_money(self):
        self.change(-125)
        first = self.state()
        self.change(-125)
        self.assertEqual(first, self.state())
        self.assertEqual(first[0], {"balance_minor": 875, "coins": 123, "medals": 7})
        self.assertEqual(first[1][-1]["balance_after_minor"], 875)

    def test_changed_amount_for_journal_reference_is_rejected(self):
        self.change(-125)
        before = self.state()
        with self.assertRaisesRegex(RuntimeError, "different amount"):
            self.change(-126)
        self.assertEqual(self.state(), before)

    def test_zero_entry_is_idempotent_and_cannot_later_change_value(self):
        self.change(0)
        self.change(0)
        self.assertEqual(len(self.state()[1]), 2)
        with self.assertRaises(RuntimeError):
            self.change(1)
        self.assertEqual(self.state()[0]["balance_minor"], 1000)

    def test_insufficient_funds_roll_back_entire_command_and_journal(self):
        before = self.state()
        with self.assertRaises(GameError) as caught:
            with self.database.transaction() as db:
                wallet.change(db, "tg:1", 100, "bonus", "attempt", 2)
                wallet.change(db, "tg:1", -1200, "purchase", "attempt", 2)
        self.assertEqual(caught.exception.code, "insufficient_funds")
        self.assertEqual(self.state(), before)

    def test_later_failure_rolls_back_successful_wallet_write(self):
        before = self.state()
        with self.assertRaises(RuntimeError):
            with self.database.transaction() as db:
                wallet.change(db, "tg:1", -100, "purchase", "attempt", 2)
                raise RuntimeError("Simulated failure creating item")
        self.assertEqual(self.state(), before)

    def test_competing_retries_record_one_credit(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.change(75), range(2)))
        self.assertEqual(self.state()[0]["balance_minor"], 1075)
        self.assertEqual(len(self.state()[1]), 2)

    def test_competing_spends_cannot_overdraw(self):
        def spend(reference):
            try:
                self.change(-700, reference=reference)
                return True
            except GameError as error:
                self.assertEqual(error.code, "insufficient_funds")
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(spend, ("a", "b")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.state()[0]["balance_minor"], 300)

    def test_invalid_or_overflowing_units_cannot_reach_sqlite(self):
        before = self.state()
        for amount in (True, 1.25, "100", wallet.MAX_MINOR_UNITS + 1, wallet.MAX_MINOR_UNITS):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    self.change(amount)
        self.assertEqual(self.state(), before)

    def test_missing_player_does_not_create_a_journal(self):
        before = self.state()
        with self.assertRaises(GameError) as caught:
            with self.database.transaction() as db:
                wallet.change(db, "tg:missing", 100, "welcome", "missing", 2)
        self.assertEqual(caught.exception.code, "player_not_found")
        self.assertEqual(self.state(), before)

    def test_autocommit_wallet_call_is_rejected(self):
        with closing(self.database.connect()) as db:
            with self.assertRaisesRegex(RuntimeError, "active transaction"):
                wallet.change(db, "tg:1", 100, "bonus", "outside-transaction", 2)
        self.assertEqual(self.state()[0]["balance_minor"], 1000)
