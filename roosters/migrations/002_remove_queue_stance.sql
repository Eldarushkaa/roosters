-- Waiting players have not paid an entry fee. Preserve their place and timing;
-- their eventual match now uses v2. Historical battle snapshots stay untouched.
CREATE TABLE queue_without_stance (
 player_id TEXT PRIMARY KEY REFERENCES players(id), power INTEGER NOT NULL,
 joined_at REAL NOT NULL, expires_at REAL NOT NULL, deadline REAL NOT NULL
);
INSERT INTO queue_without_stance(player_id,power,joined_at,expires_at,deadline)
SELECT player_id,power,joined_at,expires_at,deadline FROM queue;
DROP TABLE queue;
ALTER TABLE queue_without_stance RENAME TO queue;
CREATE INDEX queue_expiry ON queue(expires_at);
