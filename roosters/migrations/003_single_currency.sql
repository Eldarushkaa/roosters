-- Legacy coins, medals and ledger are an immutable archive. The Python hook
-- journals the opening value in minor units before this migration is marked.
ALTER TABLE players ADD COLUMN balance_minor INTEGER NOT NULL DEFAULT 0
 CHECK(typeof(balance_minor)='integer' AND balance_minor>=0);
ALTER TABLE players ADD COLUMN first_battle_used INTEGER NOT NULL DEFAULT 0
 CHECK(first_battle_used IN (0,1));
ALTER TABLE players ADD COLUMN pvp_wins INTEGER NOT NULL DEFAULT 0 CHECK(pvp_wins>=0);
ALTER TABLE players ADD COLUMN power_cache INTEGER NOT NULL DEFAULT 100 CHECK(power_cache>=0);
CREATE TABLE coin_ledger (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 player_id TEXT NOT NULL REFERENCES players(id),
 delta_minor INTEGER NOT NULL CHECK(typeof(delta_minor)='integer'),
 balance_after_minor INTEGER NOT NULL CHECK(typeof(balance_after_minor)='integer' AND balance_after_minor>=0),
 reason TEXT NOT NULL, reference TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(player_id,reason,reference)
);
-- A waiting player has not paid. A new stake needs a new explicit search.
DELETE FROM queue;
ALTER TABLE queue ADD COLUMN stake_minor INTEGER NOT NULL DEFAULT 1000
 CHECK(typeof(stake_minor)='integer' AND stake_minor>0);
CREATE INDEX queue_stake_power ON queue(stake_minor,power,joined_at);
ALTER TABLE battles ADD COLUMN stake_minor INTEGER NOT NULL DEFAULT 0
 CHECK(typeof(stake_minor)='integer' AND stake_minor>=0);
ALTER TABLE battles ADD COLUMN win_payout_minor INTEGER NOT NULL DEFAULT 0
 CHECK(typeof(win_payout_minor)='integer' AND win_payout_minor>=0);
ALTER TABLE battles ADD COLUMN created_at REAL NOT NULL DEFAULT 0;
CREATE INDEX players_power_ranking ON players(is_dev,power_cache DESC,pvp_wins DESC,id);
CREATE INDEX players_pvp_ranking ON players(is_dev,pvp_wins DESC,power_cache DESC,id);
