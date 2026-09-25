"""Launcher reliability tests. Telegram is never contacted by this suite."""

import io
import json
import traceback
import urllib.error

import pytest

from scripts.bot import Poller, TelegramAPIError, TelegramClient, handle_update, validate_webapp_url


APP_URL = "https://arena.example/game"


class FakeClient:
    def __init__(self, updates=None, send_error=None):
        self.updates = updates or []
        self.send_error = send_error
        self.calls = []

    def call(self, method, payload, timeout=35):
        self.calls.append((method, payload))
        if method == "getUpdates":
            return self.updates
        if self.send_error is not None:
            raise self.send_error
        return True


def update(update_id=12, text="/start", chat_type="private"):
    return {"update_id": update_id, "message": {"chat": {"id": 123, "type": chat_type}, "text": text}}


@pytest.mark.parametrize("command", ["/start", "/help", "/play", "/start invitation", "/start@ArenaBot"])
def test_commands_open_private_mini_app(command):
    client = FakeClient()
    handle_update(client, update(text=command), APP_URL, "ArenaBot")
    method, payload = client.calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == 123
    assert payload["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == APP_URL


@pytest.mark.parametrize("webapp_url", ["", " \n", "https://your-domain.example", "https://your-domain.example/"])
@pytest.mark.parametrize("command", ["/start", "/help", "/play"])
def test_polling_without_configured_app_confirms_bot_and_omits_dead_button(webapp_url, command):
    client = FakeClient([update(text=command)])
    poller = Poller(client, webapp_url, "ArenaBot")

    poller.poll_once()

    assert poller.offset == 13
    method, payload = client.calls[-1]
    assert method == "sendMessage"
    assert payload["chat_id"] == 123
    assert "Бот на связи" in payload["text"]
    assert "Арена пока не подключена" in payload["text"]
    assert "reply_markup" not in payload


def test_polling_with_configured_app_keeps_launch_button():
    client = FakeClient([update()])
    Poller(client, APP_URL, "ArenaBot").poll_once()
    assert client.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == APP_URL


@pytest.mark.parametrize("url", [
    "http://arena.example", "https://", "arena.example", "http://your-domain.example",
    "https://user:secret@arena.example", "https://arena.example/#fragment",
])
def test_polling_rejects_malformed_urls_instead_of_treating_them_as_unconfigured(url):
    client = FakeClient()
    with pytest.raises(ValueError):
        Poller(client, url)
    assert client.calls == []


@pytest.mark.parametrize("incoming", [
    {}, {"message": None}, {"message": "invalid"}, {"edited_message": {}},
    update(text=""), update(text="hello"), update(text="/start@OtherBot"), update(chat_type="group"),
])
def test_unknown_or_ineligible_messages_are_ignored(incoming):
    client = FakeClient()
    handle_update(client, incoming, APP_URL, "ArenaBot")
    assert client.calls == []


def test_offset_advances_after_success_and_skips_duplicates():
    client = FakeClient([update(10), update(11, "/ignored")])
    poller = Poller(client, APP_URL)
    poller.poll_once()
    assert poller.offset == 12
    poller.poll_once()
    assert sum(method == "sendMessage" for method, _ in client.calls) == 1
    assert client.calls[-1][1]["offset"] == 12


def test_transient_send_failure_does_not_acknowledge_update():
    client = FakeClient([update(10), update(11)], TelegramAPIError(429))
    poller = Poller(client, APP_URL)
    with pytest.raises(TelegramAPIError):
        poller.poll_once()
    assert poller.offset == 0
    client.send_error = None
    poller.poll_once()
    assert poller.offset == 12


def test_permanent_send_failure_does_not_poison_queue():
    client = FakeClient([update(10), update(11)], TelegramAPIError(403))
    poller = Poller(client, APP_URL)
    poller.poll_once()
    assert poller.offset == 12


def test_revoked_token_is_not_acknowledged_as_bad_message():
    client = FakeClient([update(10)], TelegramAPIError(401))
    poller = Poller(client, APP_URL)
    with pytest.raises(TelegramAPIError):
        poller.poll_once()
    assert poller.offset == 0


@pytest.mark.parametrize("url", ["", "http://arena.example", "https://user:secret@arena.example", "https://arena.example/#fragment"])
def test_invalid_webapp_urls_are_rejected(url):
    with pytest.raises(ValueError):
        validate_webapp_url(url)


def test_transport_sends_json_post_with_timeout():
    requests = []

    def open_mock(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(b'{"ok":true,"result":[{"update_id":17}]}')

    client = TelegramClient("test-secret", opener=open_mock)
    assert client.call("getUpdates", {"timeout": 25}) == [{"update_id": 17}]
    request, timeout = requests[0]
    assert timeout == 35
    assert request.method == "POST"
    assert json.loads(request.data) == {"timeout": 25}


@pytest.mark.parametrize("raw", [b"garbage", b"null", b"[]", b'{"ok":false,"error_code":429,"parameters":{"retry_after":4}}'])
def test_transport_rejects_bad_response_without_copying_body(raw):
    client = TelegramClient("test-secret", opener=lambda *args, **kwargs: io.BytesIO(raw))
    with pytest.raises(TelegramAPIError) as caught:
        client.call("getUpdates", {})
    assert "test-secret" not in str(caught.value)
    assert caught.value.retryable


def test_http_exception_does_not_disclose_token():
    def open_mock(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "SECRET-TOKEN", {}, None)

    client = TelegramClient("SECRET-TOKEN", opener=open_mock)
    with pytest.raises(TelegramAPIError) as caught:
        client.call("getUpdates", {})
    assert caught.value.code == 401
    assert not caught.value.retryable
    assert "SECRET-TOKEN" not in str(caught.value)
    rendered = "".join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    assert "SECRET-TOKEN" not in rendered


def test_network_exception_is_sanitized_and_retryable():
    def open_mock(request, timeout):
        raise urllib.error.URLError(request.full_url)

    client = TelegramClient("SECRET-TOKEN", opener=open_mock)
    with pytest.raises(TelegramAPIError) as caught:
        client.call("getUpdates", {})
    assert caught.value.retryable
    assert "SECRET-TOKEN" not in str(caught.value)


def test_http_rate_limit_preserves_retry_delay_without_description():
    def open_mock(request, timeout):
        body = io.BytesIO(b'{"description":"SECRET-TOKEN","parameters":{"retry_after":17}}')
        raise urllib.error.HTTPError(request.full_url, 429, "limited", {}, body)

    client = TelegramClient("SECRET-TOKEN", opener=open_mock)
    with pytest.raises(TelegramAPIError) as caught:
        client.call("getUpdates", {})
    assert caught.value.retry_after == 17
    assert caught.value.retryable
    assert "SECRET-TOKEN" not in str(caught.value)
