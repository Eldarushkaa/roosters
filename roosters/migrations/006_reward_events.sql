-- Read the latest wallet events for one player without sorting their full history.
CREATE INDEX coin_ledger_player_events ON coin_ledger(player_id,id DESC);
