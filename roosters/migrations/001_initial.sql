CREATE TABLE IF NOT EXISTS players (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, is_dev INTEGER NOT NULL CHECK(is_dev IN (0,1)),
 coins INTEGER NOT NULL DEFAULT 0 CHECK(coins >= 0), medals INTEGER NOT NULL DEFAULT 0 CHECK(medals >= 0),
 xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0), breed_id TEXT NOT NULL DEFAULT 'yard',
 gear TEXT NOT NULL DEFAULT '{"helmet":0,"armor":0,"sword":0}', owned_breeds TEXT NOT NULL DEFAULT '["yard"]',
 wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0, battles INTEGER NOT NULL DEFAULT 0,
 ranked_battles INTEGER NOT NULL DEFAULT 0, energy REAL NOT NULL DEFAULT 120, energy_at REAL NOT NULL,
 passive_at REAL NOT NULL, daily_at REAL NOT NULL DEFAULT 0, last_seen REAL NOT NULL, created_at REAL NOT NULL,
 battle_id TEXT, referral_code TEXT NOT NULL UNIQUE, inviter_id TEXT REFERENCES players(id),
 referral_paid INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ledger (
 id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL REFERENCES players(id),
 currency TEXT NOT NULL CHECK(currency IN ('coins','medals')), delta INTEGER NOT NULL,
 balance_after INTEGER NOT NULL CHECK(balance_after >= 0), reason TEXT NOT NULL, reference TEXT NOT NULL,
 created_at REAL NOT NULL, UNIQUE(player_id,currency,reason,reference)
);
CREATE TABLE IF NOT EXISTS commands (
 player_id TEXT NOT NULL REFERENCES players(id), key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 result TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(player_id,key)
);
CREATE TABLE IF NOT EXISTS battles (
 id TEXT PRIMARY KEY, player_a TEXT NOT NULL REFERENCES players(id), player_b TEXT REFERENCES players(id),
 mode TEXT NOT NULL CHECK(mode IN ('bot','practice','online')), rules_version TEXT NOT NULL,
 snapshot_a TEXT NOT NULL, snapshot_b TEXT NOT NULL, draw REAL NOT NULL,
 starts_at REAL NOT NULL, ends_at REAL NOT NULL, taps_a INTEGER NOT NULL DEFAULT 0, taps_b INTEGER NOT NULL DEFAULT 0,
 tap_budget_a REAL NOT NULL DEFAULT 0, tap_budget_b REAL NOT NULL DEFAULT 0,
 tap_at_a REAL NOT NULL, tap_at_b REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','finished')),
 probability REAL, winner_id TEXT, result_a TEXT, result_b TEXT
);
CREATE TABLE IF NOT EXISTS queue (
 player_id TEXT PRIMARY KEY REFERENCES players(id), stance TEXT NOT NULL, power INTEGER NOT NULL,
 joined_at REAL NOT NULL, expires_at REAL NOT NULL, deadline REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS players_presence ON players(is_dev,last_seen);
CREATE INDEX IF NOT EXISTS battles_due ON battles(status,ends_at);
CREATE INDEX IF NOT EXISTS battles_player_a ON battles(player_a,ends_at);
CREATE INDEX IF NOT EXISTS battles_player_b ON battles(player_b,ends_at);
CREATE INDEX IF NOT EXISTS queue_expiry ON queue(expires_at);

