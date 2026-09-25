"""Application use cases, deliberately independent of Flask and Telegram.

Every command, its wallet journal and its replay record share one transaction.
Clients send intentions only. Snapshots freeze combat stats at entry; sessions,
multiple tabs, disconnects and retries cannot create a second settlement.
"""

import hashlib
import json
import math
import secrets
import time
import uuid
from contextlib import closing

from . import rules, rules_v1, rules_v2, rules_v3, rules_v4, rules_v5, wallet
from .errors import GameError
from .rules import MIN_STAKE_MINOR, MAX_STAKE_MINOR, STAKE_STEP_MINOR


# Monetary storage and settlement distinguish these two archived formats from
# the minor-unit wager format. Never compare to the *current* release label:
# a new release must not reinterpret a committed minor-unit stake as medals.
LEGACY_ECONOMY_VERSIONS = frozenset({"v1", "v2"})


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GameService:
    PRESENCE_TTL = 45
    QUEUE_TTL = 60
    QUEUE_DEADLINE = 120

    def __init__(self, database, clock=time.time, random_float=None):
        self.database = database
        self.clock = clock
        self.random_float = random_float or (lambda: secrets.randbelow(10**12) / 10**12)
        # Persisted matches keep their original timing, odds and rewards.
        self.engines = {rules_v1.RULES_VERSION: rules_v1, rules_v2.RULES_VERSION: rules_v2,
                        rules_v3.RULES_VERSION: rules_v3, rules_v4.RULES_VERSION: rules_v4,
                        rules_v5.RULES_VERSION: rules_v5, rules.RULES_VERSION: rules}

    def register(self, player_id, name, is_dev, referral=""):
        now = self.clock()
        with self.database.transaction() as db:
            existing = db.execute("SELECT id FROM players WHERE id=?", (player_id,)).fetchone()
            if not existing:
                inviter = db.execute("SELECT id FROM players WHERE referral_code=? AND is_dev=0 AND id<>?",
                                     (referral, player_id)).fetchone() if not is_dev else None
                db.execute("""INSERT INTO players(id,name,is_dev,energy_at,passive_at,last_seen,created_at,referral_code,inviter_id)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                           (player_id, name, int(is_dev), now, now, now, now,
                            "ref_" + secrets.token_hex(6), inviter["id"] if inviter else None))
                wallet.change(db, player_id, rules.WELCOME_MINOR, "welcome", player_id, now)
            else:
                db.execute("UPDATE players SET name=?,last_seen=? WHERE id=?", (name, now, player_id))
            self._maintenance(db, now)
            return self._state(db, player_id, now)

    def state(self, player_id):
        with self.database.transaction() as db:
            now = self.clock()
            self._maintenance(db, now)
            return self._state(db, player_id, now)

    def tick(self):
        """Run by the optional worker; HTTP recovery uses the same exact path."""
        with self.database.transaction() as db:
            self._maintenance(db, self.clock())

    def command(self, player_id, key, operation, payload):
        try:
            uuid.UUID(key)
        except (ValueError, TypeError, AttributeError):
            raise GameError("idempotency_key_required", "Для команды нужен Idempotency-Key в формате UUID.") from None
        fingerprint = hashlib.sha256(encode([operation, payload]).encode()).hexdigest()
        with self.database.transaction() as db:
            now = self.clock()
            self._player(db, player_id)
            self._maintenance(db, now)
            previous = db.execute("SELECT fingerprint,result FROM commands WHERE player_id=? AND key=?",
                                  (player_id, key)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise GameError("idempotency_conflict", "Эта команда уже использована с другими параметрами.", 409)
                result = json.loads(previous["result"])
            else:
                result = self._execute(db, player_id, operation, payload, key, now)
                db.execute("INSERT INTO commands VALUES (?,?,?,?,?)", (player_id, key, fingerprint, encode(result), now))
            # Replayed commands retain their result but carry fresh state. A stale
            # lost response must never roll the UI balance or battle backwards.
            return {"result": result, "state": self._state(db, player_id, now)}

    @staticmethod
    def _player(db, player_id):
        row = db.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
        if not row:
            raise GameError("player_not_found", "Игрок не найден. Войдите снова.", 401)
        return row

    @staticmethod
    def _fields(payload, expected):
        if not isinstance(payload, dict) or set(payload) != set(expected):
            raise GameError("invalid_payload", "Неверные параметры команды.")

    @staticmethod
    def _taps(payload):
        taps = payload.get("taps")
        if type(taps) is not int or not 1 <= taps <= rules.TAP_CAP:
            raise GameError("invalid_taps", f"Передайте от 1 до {rules.TAP_CAP} нажатий.")
        return taps

    def _available(self, db, player):
        active = db.execute("SELECT id FROM battles WHERE id=? AND status='active'", (player["battle_id"],)).fetchone()
        queued = db.execute("SELECT 1 FROM queue WHERE player_id=?", (player["id"],)).fetchone()
        if active or queued:
            raise GameError("player_busy", "Сначала завершите бой или поиск соперника.", 409)

    @staticmethod
    def _passive(player, now):
        seconds = min(rules.PASSIVE_CAP_SECONDS, max(0, now - player["passive_at"]))
        return int(seconds // 60) * rules.PASSIVE_PER_MINUTE_MINOR

    @staticmethod
    def _stake(value, allow_free=False):
        if allow_free and type(value) is int and value == 0:
            return 0
        try:
            return rules._stake(value)
        except ValueError:
            raise GameError("invalid_stake", "Ставка должна быть от 10 монет, с шагом 0,01 и в пределах доступного максимума.") from None

    @staticmethod
    def _stake_limits(player):
        # Keep current JSON integers and even the largest eventual prize exact.
        # The additional headroom bound prevents a high pre-existing SQLite
        # balance from overflowing when its stake returns with up to 116% net.
        balance = player["balance_minor"]
        sqlite_headroom = (wallet.MAX_MINOR_UNITS - balance) * 25 // 29
        return {"min_minor": MIN_STAKE_MINOR,
                "max_minor": min(balance, MAX_STAKE_MINOR, sqlite_headroom),
                "step_minor": STAKE_STEP_MINOR}

    def _funded_stake(self, player, value, allow_free=False):
        stake = self._stake(value, allow_free)
        if stake > player["balance_minor"]:
            raise GameError("insufficient_funds", "Недостаточно монет для этой ставки.", 409)
        if stake > self._stake_limits(player)["max_minor"]:
            raise GameError("invalid_stake", "Ставка превышает доступный максимум.")
        return stake

    def battle_quote(self, player_id, stake_minor):
        """Read an advisory quote without settlement, random draws or writes.

        Entry still checks current funds and expected_power in its transaction;
        a quote cannot reserve funds or authorize a stale/changed-power start.
        """
        with closing(self.database.connect()) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            player = self._player(db, player_id)
            stake = self._funded_stake(player, stake_minor)
            power = self._power(player)
            lower, upper = rules.bot_power_bounds(power)
            return {"rules_version": rules.RULES_VERSION, "power": power, "stake_minor": stake,
                    "bot": {"min_payout_minor": rules.bot_payout(stake, power, lower),
                            "max_payout_minor": rules.bot_payout(stake, power, upper)},
                    "online": {"win_payout_minor": rules.online_payout(stake)}}

    def _execute(self, db, pid, op, body, key, now):
        player = self._player(db, pid)
        if op == "presence":
            self._fields(body, [])
            db.execute("UPDATE players SET last_seen=? WHERE id=?", (now, pid))
            db.execute("UPDATE queue SET expires_at=MIN(deadline,?) WHERE player_id=?", (now + self.QUEUE_TTL, pid))
            return {"ok": True}
        if op == "claim/passive":
            self._fields(body, [])
            amount = self._passive(player, now)
            if not amount:
                raise GameError("nothing_to_claim", "До первых монет осталось меньше минуты.", 409)
            wallet.change(db, pid, amount, "passive", key, now)
            # Retain the fractional minute without retaining time beyond the cap.
            remainder = max(0, now - player["passive_at"]) % 60
            db.execute("UPDATE players SET passive_at=? WHERE id=?", (now - remainder, pid))
            return {"payout_minor": amount}
        if op == "claim/daily":
            self._fields(body, [])
            if player["daily_at"] and now < player["daily_at"] + 86400:
                raise GameError("daily_not_ready", "Ежедневная награда ещё восстанавливается.", 409)
            wallet.change(db, pid, rules.DAILY_MINOR, "daily", key, now)
            db.execute("UPDATE players SET daily_at=? WHERE id=?", (now, pid))
            return {"payout_minor": rules.DAILY_MINOR}
        if op == "gear/upgrade":
            self._fields(body, ["slot"])
            self._available(db, player)
            slot = body["slot"]
            gear = json.loads(player["gear"])
            if not isinstance(slot, str) or slot not in gear:
                raise GameError("invalid_slot", "Неизвестный предмет снаряжения.")
            cost = rules.upgrade_cost(gear[slot])
            if cost is None:
                raise GameError("max_level", "Достигнут максимальный уровень предмета.", 409)
            wallet.change(db, pid, -cost, "upgrade", key, now)
            gear[slot] += 1
            db.execute("UPDATE players SET gear=? WHERE id=?", (encode(gear), pid))
            self._refresh_power(db, pid)
            return {"slot": slot, "level": gear[slot], "cost_minor": cost}
        if op == "breed/buy":
            self._fields(body, ["breed_id"])
            self._available(db, player)
            breed = next((b for b in rules.BREEDS if b["id"] == body["breed_id"]), None)
            if not breed:
                raise GameError("invalid_breed", "Неизвестная порода.")
            owned = json.loads(player["owned_breeds"])
            if breed["id"] not in owned:
                wallet.change(db, pid, -breed["price_minor"], "breed", key, now)
                owned.append(breed["id"])
            db.execute("UPDATE players SET breed_id=?,owned_breeds=? WHERE id=?", (breed["id"], encode(owned), pid))
            self._refresh_power(db, pid)
            return {"breed_id": breed["id"]}
        if op == "battle/start":
            self._fields(body, ["mode", "stake_minor", "expected_power"])
            self._available(db, player)
            if body["mode"] != "bot":
                raise GameError("invalid_mode", "Для онлайн-боя найдите соперника.")
            if type(body["expected_power"]) is not int or body["expected_power"] != self._power(player):
                raise GameError("power_changed", "Мощь изменилась. Обновите арену перед ставкой.", 409)
            stake = self._funded_stake(player, body["stake_minor"], allow_free=True)
            if stake == 0 and player["first_battle_used"]:
                raise GameError("free_battle_used", "Первый бесплатный бой уже использован.", 409)
            battle_id = self._start_battle(db, player, None, "bot", stake, now)
            return {"battle_id": battle_id}
        if op == "queue/join":
            self._fields(body, ["stake_minor"])
            self._available(db, player)
            stake = self._funded_stake(player, body["stake_minor"])
            strength = self._power(player)
            candidate = db.execute("""SELECT q.* FROM queue q JOIN players p ON p.id=q.player_id
                WHERE p.is_dev=? AND p.last_seen>? AND p.balance_minor>=q.stake_minor AND q.expires_at>?
                AND p.balance_minor<=?-((29*q.stake_minor+24)/25)
                ORDER BY q.joined_at,q.player_id LIMIT 1""",
                (player["is_dev"], now-self.PRESENCE_TTL, now, wallet.MAX_MINOR_UNITS)).fetchone()
            if candidate:
                opponent = self._player(db, candidate["player_id"])
                battle_id = self._start_battle(db, opponent, player, "online", candidate["stake_minor"], now, stake_b=stake)
                db.execute("DELETE FROM queue WHERE player_id=?", (opponent["id"],))
                return {"battle_id": battle_id, "matched": True}
            db.execute("INSERT INTO queue(player_id,power,joined_at,expires_at,deadline,stake_minor) VALUES (?,?,?,?,?,?)",
                       (pid, strength, now, now+self.QUEUE_TTL, now+self.QUEUE_DEADLINE, stake))
            return {"matched": False}
        if op == "queue/leave":
            self._fields(body, [])
            db.execute("DELETE FROM queue WHERE player_id=?", (pid,))
            return {"ok": True}
        if op == "battle/tap":
            self._fields(body, ["battle_id", "taps"])
            taps = self._taps(body)
            if not isinstance(body["battle_id"], str):
                raise GameError("invalid_battle", "Неверный идентификатор боя.")
            battle = db.execute("SELECT * FROM battles WHERE id=?", (body["battle_id"],)).fetchone()
            if not battle or pid not in (battle["player_a"], battle["player_b"]):
                raise GameError("battle_not_found", "Бой не найден.", 404)
            if battle["status"] != "active" or now >= battle["ends_at"]:
                raise GameError("battle_finished", "Этот бой уже завершён.", 409)
            if now < battle["starts_at"]:
                return {"accepted": 0}
            side = "a" if pid == battle["player_a"] else "b"
            engine = self.engines[battle["rules_version"]]
            # v3 deliberately accepts the entire remaining cap at once. Only
            # ownership, combat time, request replay and the total cap apply.
            if battle["rules_version"] not in LEGACY_ECONOMY_VERSIONS:
                accepted = min(taps, engine.TAP_CAP-battle["taps_"+side])
                db.execute("UPDATE battles SET taps_"+side+"=taps_"+side+"+? WHERE id=?", (accepted, battle["id"]))
                return {"accepted": accepted}
            # Historical battles retain their original token bucket.
            budget = min(engine.TAP_BURST, battle["tap_budget_"+side] + max(0, now-battle["tap_at_"+side])*engine.TAP_RATE)
            accepted = min(taps, math.floor(budget + 1e-8), engine.TAP_CAP-battle["taps_"+side])
            db.execute("UPDATE battles SET taps_"+side+"=taps_"+side+"+?,tap_budget_"+side+"=?,tap_at_"+side+"=? WHERE id=?",
                       (accepted, budget-accepted, now, battle["id"]))
            return {"accepted": accepted}
        raise GameError("unknown_command", "Неизвестная команда.", 404)

    @staticmethod
    def _power(player):
        return rules.power(player["breed_id"], json.loads(player["gear"]), player["xp"])

    def _refresh_power(self, db, pid):
        """Maintain an indexed ranking value with every progression mutation."""
        db.execute("UPDATE players SET power_cache=? WHERE id=?", (self._power(self._player(db, pid)), pid))

    def _snapshot(self, player):
        return {"name": player["name"], "power": self._power(player), "breed_id": player["breed_id"]}

    def _start_battle(self, db, player_a, player_b, mode, stake, now, stake_b=None):
        """Commit opponent, draw, quote and both debits before roulette begins.

        The animation never chooses a new opponent. A lost response, restart or
        another tab can only recover the same immutable match and payout.
        """
        battle_id = str(uuid.uuid4())
        snap_a = self._snapshot(player_a)
        if player_b:
            snap_b = self._snapshot(player_b)
        else:
            lower, upper = rules.bot_power_bounds(snap_a["power"])
            snap_b = {"name": secrets.choice(["Клювдиатор", "Сэр Кукарек", "Полковник Зерно", "Пернатый Джо"]),
                      "power": lower + min(upper-lower, int(self.random_float()*(upper-lower+1))),
                      "breed_id": secrets.choice(rules.BREEDS)["id"]}
        payout = (rules.online_payout(stake) if player_b else
                  rules.bot_payout(stake, snap_a["power"], snap_b["power"]) if stake else rules.FREE_REWARD_MINOR)
        stake_b = (stake if stake_b is None else stake_b) if player_b else 0
        payout_b = rules.online_payout(stake_b) if player_b else 0
        for player, entry in ((player_a, stake), (player_b, stake_b)):
            if player:
                wallet.change(db, player["id"], -entry, "battle_entry", battle_id, now)
        starts = now + rules.ROULETTE_DURATION
        db.execute("""INSERT INTO battles(id,player_a,player_b,mode,rules_version,snapshot_a,snapshot_b,draw,starts_at,ends_at,tap_at_a,tap_at_b,stake_minor,win_payout_minor,stake_b_minor,win_payout_b_minor,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (battle_id, player_a["id"], player_b["id"] if player_b else None, mode, rules.RULES_VERSION,
                    encode(snap_a), encode(snap_b), self.random_float(), starts, starts+rules.BATTLE_DURATION, starts, starts,
                    stake, payout, stake_b, payout_b, now))
        for player in (player_a, player_b):
            if player:
                db.execute("UPDATE players SET battle_id=?,first_battle_used=1 WHERE id=?", (battle_id, player["id"]))
        return battle_id

    def _maintenance(self, db, now):
        db.execute("DELETE FROM queue WHERE expires_at<=? OR deadline<=?", (now, now))
        due = db.execute("SELECT * FROM battles WHERE status='active' AND ends_at<=? ORDER BY ends_at LIMIT 100", (now,)).fetchall()
        for battle in due:
            self._settle(db, battle, now)

    def _settle(self, db, battle, now):
        engine = self.engines[battle["rules_version"]]
        a, b = json.loads(battle["snapshot_a"]), json.loads(battle["snapshot_b"])
        modern = battle["rules_version"] not in LEGACY_ECONOMY_VERSIONS
        taps_b = battle["taps_b"] if battle["player_b"] else 0 if modern else engine.BOT_TAPS
        probability = float(engine.bot_probability(a["power"], b["power"], battle["taps_a"]) if modern and not battle["player_b"]
                            else engine.battle_probability(a, b, battle["taps_a"], taps_b))
        won_a = battle["draw"] < probability
        winner_id = battle["player_a"] if won_a else battle["player_b"]
        real_pvp = battle["mode"] == "online" and battle["player_b"] and not any(
            self._player(db, pid)["is_dev"] for pid in (battle["player_a"], battle["player_b"]))
        results = []
        for side, pid, won in (("a", battle["player_a"], won_a), ("b", battle["player_b"], not won_a)):
            if modern:
                stake, prize = self._wager(battle, side)
                free = stake == 0
                payout = prize if won or free else 0
                reward = dict(engine.rewards("free" if free else battle["mode"], won), won=won,
                              payout_minor=payout, net_minor=payout-stake)
            else:
                original = engine.rewards(battle["mode"], won)
                payout = (original["coins"] + original["medals"]*10)*rules.COIN_SCALE
                old_stake = 0 if battle["mode"] == "practice" else engine.ENTRY_FEE*10*rules.COIN_SCALE
                reward = dict(original, won=won, payout_minor=payout, net_minor=payout-old_stake, legacy=True)
            results.append(reward)
            if not pid:
                continue
            wallet.change(db, pid, payout, "battle_reward", battle["id"], now)
            db.execute("UPDATE players SET xp=xp+?,wins=wins+?,losses=losses+?,battles=battles+1,ranked_battles=ranked_battles+?,pvp_wins=pvp_wins+? WHERE id=?",
                       (reward["xp"], int(won), int(not won), int(battle["mode"]!="practice" and (not modern or stake > 0)),
                        int(bool(real_pvp) and won), pid))
            self._refresh_power(db, pid)
            self._referral_reward(db, pid, now)
        db.execute("UPDATE battles SET status='finished',probability=?,winner_id=?,result_a=?,result_b=?,taps_b=? WHERE id=?",
                   (probability, winner_id, encode(results[0]), encode(results[1]), taps_b, battle["id"]))

    def _referral_reward(self, db, pid, now):
        player = self._player(db, pid)
        if not player["is_dev"] and player["inviter_id"] and not player["referral_paid"] and player["ranked_battles"] >= 3:
            # Deferred credit raises the cost of empty-account invite farming.
            # It is not Sybil proof; production requires abuse monitoring.
            for beneficiary in (pid, player["inviter_id"]):
                wallet.change(db, beneficiary, rules.REFERRAL_MINOR, "referral", pid, now)
            db.execute("UPDATE players SET referral_paid=1 WHERE id=?", (pid,))

    @staticmethod
    def _wager(battle, side):
        """Return this side's immutable quote, including migrated equal stakes."""
        if side == "b" and battle["player_b"]:
            return battle["stake_b_minor"], battle["win_payout_b_minor"]
        return battle["stake_minor"], battle["win_payout_minor"]

    def _battle_view(self, battle, pid, now):
        engine = self.engines[battle["rules_version"]]
        modern = battle["rules_version"] not in LEGACY_ECONOMY_VERSIONS
        side = "a" if battle["player_a"] == pid else "b"
        other = "b" if side == "a" else "a"
        you, opponent = json.loads(battle["snapshot_"+side]), json.loads(battle["snapshot_"+other])
        you["taps"] = battle["taps_"+side]
        opponent["is_bot"] = battle["player_"+other] is None
        bot_taps = 0 if modern else engine.BOT_TAPS
        opponent["taps"] = min(bot_taps, max(0, int((now-battle["starts_at"])*bot_taps/engine.BATTLE_DURATION))) if opponent["is_bot"] else battle["taps_"+other]
        probability = battle["probability"]
        current_probability = None
        if modern:
            current_probability = float(
                engine.bot_probability(you["power"], opponent["power"], you["taps"])
                if opponent["is_bot"] else engine.win_probability(
                    you["power"], opponent["power"], you["taps"], opponent["taps"]))
        result = json.loads(battle["result_"+side]) if battle["result_"+side] else None
        stake, payout = self._wager(battle, side)
        if not modern:
            stake = 0 if battle["mode"] == "practice" else engine.ENTRY_FEE*10*rules.COIN_SCALE
            win_reward = engine.rewards(battle["mode"], True)
            payout = (win_reward["coins"]+win_reward["medals"]*10)*rules.COIN_SCALE
            if result:
                # Normalize presentation only. Saved historical JSON and the
                # old whole-unit journal remain intact for audit and replay.
                result = dict(result, payout_minor=(result["coins"]+result["medals"]*10)*rules.COIN_SCALE,
                              legacy=True)
                result["net_minor"] = result["payout_minor"]-stake
        lower, upper = rules.bot_power_bounds(you["power"])
        return {"id": battle["id"], "mode": battle["mode"], "status": battle["status"],
                "created_at": battle["created_at"] if modern else battle["starts_at"]-2,
                "starts_at": battle["starts_at"], "ends_at": battle["ends_at"], "you": you, "opponent": opponent,
                "current_win_probability": current_probability,
                "tap_cap": engine.TAP_CAP, "win_probability": None if probability is None else probability if side == "a" else 1-probability,
                "wager": {"stake_minor": stake, "win_payout_minor": payout},
                "roulette": {"min_power": lower, "max_power": upper} if modern and opponent["is_bot"] else None,
                "result": result,
                "rules_version": battle["rules_version"]}

    def _state(self, db, pid, now):
        row = self._player(db, pid)
        player = {key: row[key] for key in ("id", "name", "balance_minor", "xp", "breed_id", "wins", "pvp_wins", "losses", "battles", "referral_code")}
        player.update(is_dev=bool(row["is_dev"]), gear=json.loads(row["gear"]), owned_breeds=json.loads(row["owned_breeds"]), power=self._power(row))
        player.update(rules.progression(row["xp"]))
        queue = db.execute("SELECT joined_at,expires_at,stake_minor FROM queue WHERE player_id=?", (pid,)).fetchone()
        battle = db.execute("SELECT * FROM battles WHERE id=?", (row["battle_id"],)).fetchone()
        history = db.execute("SELECT * FROM battles WHERE (player_a=? OR player_b=?) AND status='finished' ORDER BY ends_at DESC LIMIT 10", (pid, pid)).fetchall()
        online = db.execute("SELECT is_dev,COUNT(*) AS total FROM players WHERE last_seen>? GROUP BY is_dev", (now-self.PRESENCE_TTL,)).fetchall()
        counts = {r["is_dev"]: r["total"] for r in online}
        searching = db.execute("SELECT COUNT(*) FROM queue q JOIN players p ON p.id=q.player_id WHERE p.is_dev=0 AND p.last_seen>?", (now-self.PRESENCE_TTL,)).fetchone()[0]
        lower, upper = rules.bot_power_bounds(player["power"])
        return {"server_time": now, "rules_version": rules.RULES_VERSION, "player": player,
                "economy": {"first_free_battle_available": not bool(row["first_battle_used"]),
                            "stake_limits": self._stake_limits(row),
                            "passive_available_minor": self._passive(row, now), "daily_reward_minor": rules.DAILY_MINOR,
                            "daily_available": not row["daily_at"] or now >= row["daily_at"]+86400,
                            "next_daily_at": row["daily_at"]+86400 if row["daily_at"] else 0,
                            "upgrade_costs_minor": {slot: rules.upgrade_cost(level) for slot, level in player["gear"].items()},
                            "bot_quotes": [{"stake_minor": stake, "min_payout_minor": rules.bot_payout(stake, player["power"], lower),
                                            "max_payout_minor": rules.bot_payout(stake, player["power"], upper)} for stake in rules.STAKES_MINOR],
                            "online_quotes": [{"stake_minor": stake, "win_payout_minor": rules.online_payout(stake)}
                                              for stake in rules.STAKES_MINOR]},
                "catalog": rules.catalog(), "presence": {"online": counts.get(0, 0), "development_online": counts.get(1, 0), "searching": searching},
                "queue": dict(queue) if queue else None, "battle": self._battle_view(battle, pid, now) if battle else None,
                "history": [self._battle_view(b, pid, now) for b in history]}

    def leaderboard(self, pid):
        with self.database.transaction() as db:
            self._maintenance(db, self.clock())
            player = self._player(db, pid)
            result = {"scope": "development" if player["is_dev"] else "telegram"}
            # Both orderings use indexed, transactionally maintained values.
            for name, ordering in (("power", "power_cache DESC,pvp_wins DESC,id"),
                                   ("pvp_wins", "pvp_wins DESC,power_cache DESC,id")):
                rows = db.execute("SELECT id,name,power_cache,pvp_wins,xp FROM players WHERE is_dev=? ORDER BY "+ordering+" LIMIT 50",
                                  (player["is_dev"],)).fetchall()
                result[name] = [{"id": r["id"], "name": r["name"], "power": r["power_cache"], "pvp_wins": r["pvp_wins"],
                                 "level": rules.progression(r["xp"])["level"]} for r in rows]
            return result
