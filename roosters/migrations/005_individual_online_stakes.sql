-- The original columns now describe side A. Preserve the equal-stake quote
-- of every historical online battle for side B before allowing unequal bets.
ALTER TABLE battles ADD COLUMN stake_b_minor INTEGER NOT NULL DEFAULT 0
 CHECK(typeof(stake_b_minor)='integer' AND stake_b_minor>=0);
ALTER TABLE battles ADD COLUMN win_payout_b_minor INTEGER NOT NULL DEFAULT 0
 CHECK(typeof(win_payout_b_minor)='integer' AND win_payout_b_minor>=0);
UPDATE battles SET stake_b_minor=stake_minor,win_payout_b_minor=win_payout_minor
 WHERE player_b IS NOT NULL;
-- Waiting players retain their chosen stake and FIFO position. Stakes and
-- power no longer constrain matching; no wallet or existing snapshot changes.
DROP INDEX queue_stake_power;
CREATE INDEX queue_joined ON queue(joined_at);
