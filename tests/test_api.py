"""Authentication/transport tests use signed fixtures and isolated databases."""
import hashlib
import hmac
import json
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from roosters import create_app
from roosters.auth import Sessions, validate_init_data
from roosters.config import Settings, load_env
from roosters.errors import GameError


def signed_data(token="test-bot-token", now=1800000000, user=None, **extra):
    fields = {"auth_date": str(now), "user": json.dumps(user or {"id": 12345, "first_name": "Петя"}, ensure_ascii=False), **extra}
    check = "\n".join("{}={}".format(k, fields[k]) for k in sorted(fields))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramAuthTests(unittest.TestCase):
    def test_valid_signature_preserves_verified_referral(self):
        user = validate_init_data(signed_data(start_param="ref_test"), "test-bot-token", now=1800000000)
        self.assertEqual(user, {"id": "tg:12345", "name": "Петя", "is_dev": False, "referral": "ref_test"})

    def test_modern_signature_field_participates_in_hmac(self):
        user = validate_init_data(signed_data(signature="external-ed25519"), "test-bot-token", now=1800000000)
        self.assertEqual(user["id"], "tg:12345")

    def test_tampering_duplicates_wrong_token_and_missing_data_fail(self):
        valid = signed_data()
        for raw, token in [(valid.replace("12345", "99999"), "test-bot-token"),
                           (valid+"&auth_date=1800000000", "test-bot-token"), (valid, "wrong"),
                           ("", "test-bot-token"), ("user=null", "test-bot-token"),
                           ("malformed", "test-bot-token"), ([], "test-bot-token")]:
            with self.subTest(raw=str(raw)[:40]), self.assertRaises(GameError):
                validate_init_data(raw, token, now=1800000000)

    def test_expired_and_far_future_dates_rejected(self):
        for timestamp in (1799996399, 1800000031):
            with self.subTest(timestamp=timestamp), self.assertRaises(GameError):
                validate_init_data(signed_data(now=timestamp), "test-bot-token", now=1800000000)

    def test_bot_and_invalid_user_ids_rejected(self):
        for user in ({"id": True, "first_name": "X"}, {"id": -1, "first_name": "X"},
                     {"id": 12, "first_name": []}, {"id": "12", "first_name": "X"},
                     {"id": 12, "first_name": "X", "is_bot": True}):
            with self.subTest(user=user), self.assertRaises(GameError):
                validate_init_data(signed_data(user=user), "test-bot-token", now=1800000000)

    def test_session_expires_and_tampering_fails(self):
        sessions = Sessions("a"*48)
        with patch("itsdangerous.timed.time.time", return_value=1800000000):
            token = sessions.issue("tg:1")
            self.assertEqual(sessions.verify(token), "tg:1")
            with self.assertRaises(GameError):
                sessions.verify(token + "x")
        with patch("itsdangerous.timed.time.time", return_value=1800086401), self.assertRaises(GameError):
            sessions.verify(token)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1800000000.0
        self.settings = Settings(app_env="test", allow_dev_auth=True, secret_key="secret"*8,
                                 database_path=str(Path(self.temp.name)/"game.sqlite3"), bot_token="test-bot-token")
        self.app = create_app(self.settings, clock=lambda: self.now, random_float=lambda: 0.25)
        self.client = self.app.test_client()
        result = self.client.post("/api/v1/auth/dev", json={"user_id": "test1", "name": "Первый"})
        self.assertEqual(result.status_code, 200)
        self.token = result.json["token"]

    def headers(self, key=None):
        return {"Authorization": "Bearer "+self.token, "Idempotency-Key": key or str(uuid.uuid4())}

    def test_health_config_and_static_page(self):
        self.assertEqual(self.client.get("/healthz").json["status"], "ok")
        self.assertTrue(self.client.get("/api/v1/config").json["dev_auth"])
        for path in ("/", "/static/app.js", "/static/styles.css"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Content-Security-Policy", response.headers)
            response.close()

    def test_auth_required_no_secret_or_draw_exposed(self):
        self.assertEqual(self.client.get("/api/v1/state").status_code, 401)
        response = self.client.post("/api/v1/battle/start", json={"mode": "bot", "stake_minor": 1000, "expected_power": 100}, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json["state"]["battle"]["win_probability"])
        self.assertNotIn("draw", response.get_data(as_text=True))
        self.assertNotIn("test-bot-token", response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_request_key_and_extra_economic_inputs_rejected(self):
        response = self.client.post("/api/v1/claim/daily", json={}, headers={"Authorization": "Bearer "+self.token})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/v1/claim/daily", json={"payout_minor": 100000}, headers=self.headers())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/v1/state", headers=self.headers()).json["player"]["balance_minor"], 42000)

    def test_json_errors_are_stable_and_non_json_is_rejected(self):
        for data, ctype, expected in [("{", "application/json", 400), ("[]", "application/json", 400),
                                      ("taps=1", "application/x-www-form-urlencoded", 415),
                                      ("x"*40000, "application/json", 413)]:
            response = self.client.post("/api/v1/train", data=data, content_type=ctype, headers=self.headers())
            self.assertEqual(response.status_code, expected)
            self.assertIn("error", response.json)

    def test_cross_origin_and_nonlocal_dev_requests_blocked(self):
        headers = dict(self.headers(), Origin="https://malicious.example")
        self.assertEqual(self.client.post("/api/v1/train", json={"taps": 1}, headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/config", base_url="http://evil.example").status_code, 403)

    def test_replay_survives_app_restart_without_second_upgrade(self):
        headers = self.headers()
        response = self.client.post("/api/v1/gear/upgrade", json={"slot": "sword"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        restarted = create_app(self.settings, clock=lambda: self.now).test_client()
        again = restarted.post("/api/v1/gear/upgrade", json={"slot": "sword"}, headers=headers)
        self.assertEqual(again.json["state"]["player"]["balance_minor"], 34000)
        self.assertEqual(again.json["state"]["player"]["gear"]["sword"], 1)

    def test_signed_telegram_login_counts_real_account_only_once(self):
        for _ in range(2):
            result = self.client.post("/api/v1/auth/telegram", json={"init_data": signed_data()})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["state"]["player"]["balance_minor"], 42000)
            self.assertEqual(result.json["state"]["presence"], {"online": 1, "development_online": 1, "searching": 0})

    def test_dev_session_rejected_after_development_auth_disabled(self):
        client = create_app(replace(self.settings, allow_dev_auth=False), clock=lambda: self.now).test_client()
        self.assertEqual(client.post("/api/v1/auth/dev", json={"user_id": "x", "name": "x"}).status_code, 403)
        self.assertEqual(client.get("/api/v1/state", headers=self.headers()).status_code, 403)

    def test_unknown_route_does_not_crash_or_mutate(self):
        response = self.client.post("/api/v1/unknown", json={}, headers=self.headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"]["code"], "unknown_command")

    def test_read_only_custom_quote_needs_auth_but_no_command_key(self):
        path = "/api/v1/battle/quote?stake_minor=12345"
        self.assertEqual(self.client.get(path).status_code, 401)
        response = self.client.get(path, headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.json, {"rules_version": "v6", "power": 100, "stake_minor": 12345,
                                        "bot": {"min_payout_minor": 18887, "max_payout_minor": 26665},
                                        "online": {"win_payout_minor": 23455}})
        state = self.client.get("/api/v1/state", headers=self.headers()).json
        self.assertEqual(state["economy"]["stake_limits"], {"min_minor": 1000, "max_minor": 42000, "step_minor": 1})
        self.assertTrue(state["economy"]["first_free_battle_available"])
        self.assertIsNone(state["battle"])
        with self.app.extensions["database"].transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM commands").fetchone()[0], 0)

    def test_quote_rejects_ambiguous_query_and_fractional_or_unfunded_stakes(self):
        invalid = ["", "stake_minor=1000&stake_minor=1001", "stake_minor=1000&power=100",
                   "stake_minor=1000.0", "stake_minor=1e3", "stake_minor=true", "stake_minor=-1000",
                   "stake_minor=", "stake_minor=999", "stake_minor=0", "stake_minor=%201000",
                   "stake_minor=%2B1000", "stake_minor=999999999999999999999999999999"]
        for query in invalid:
            with self.subTest(query=query):
                result = self.client.get("/api/v1/battle/quote?" + query, headers=self.headers())
                self.assertEqual(result.status_code, 400)
        result = self.client.get("/api/v1/battle/quote?stake_minor=42001", headers=self.headers())
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json["error"]["code"], "insufficient_funds")

    def test_custom_stake_transport_accepts_integer_minor_units_and_rejects_fractional_json(self):
        for invalid in (True, 1001.0, 999, None, "1001"):
            response = self.client.post("/api/v1/battle/start",
                json={"mode": "bot", "stake_minor": invalid, "expected_power": 100}, headers=self.headers())
            self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/v1/battle/start",
            json={"mode": "bot", "stake_minor": 42000, "expected_power": 100}, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["player"]["balance_minor"], 0)
        self.assertEqual(response.json["state"]["battle"]["wager"]["stake_minor"], 42000)


class ConfigTests(unittest.TestCase):
    def test_production_fails_closed(self):
        valid = Settings(app_env="production", secret_key="s"*48, bot_token="dummy", webapp_url="https://play.example.org")
        valid.validate()
        for settings in (replace(valid, allow_dev_auth=True), replace(valid, bot_token=""),
                         replace(valid, secret_key="short"), replace(valid, webapp_url="http://example.org"),
                         replace(valid, webapp_url="https://your-domain.example")):
            with self.assertRaises(ValueError):
                settings.validate()

    def test_env_parser_does_not_override_or_execute(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"KEEP": "original"}):
            path = Path(temp)/".env"
            path.write_text("KEEP=new\nOTHER='$(echo never-run)'\n# COMMENT=no\n", encoding="utf-8")
            load_env(path)
            import os
            self.assertEqual(os.environ["KEEP"], "original")
            self.assertEqual(os.environ["OTHER"], "$(echo never-run)")
