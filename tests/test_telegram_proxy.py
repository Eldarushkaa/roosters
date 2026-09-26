"""SOCKS transport and entrypoint checks with no external network or real secrets."""

import io
import socket
import ssl
import traceback
import urllib.request
from unittest.mock import Mock

import pytest
import socks

from roosters.config import Settings
from scripts import bot, set_menu
from scripts.telegram_proxy import telegram_opener


PROXY = "socks5://test-user:test-password@proxy.example:1080"


@pytest.mark.parametrize("url", ["", "  "])
def test_empty_proxy_preserves_default_transport(url):
    assert telegram_opener(url) is urllib.request.urlopen


@pytest.mark.parametrize("url", [
    "http://user:secret@proxy.example:1080", "socks4://proxy.example",
    "socks5://", "socks5://user:secret@proxy.example:bad",
    "socks5://proxy.example:0", "socks5://proxy.example:65536",
    "socks5://user@proxy.example", "socks5://user:@proxy.example",
    "socks5://:secret@proxy.example", "socks5://proxy.example/path",
    "socks5://proxy.example?secret", "socks5://proxy.example#secret",
    "socks5://proxy.example\n", "socks5://[broken",
    "socks5://user:%FF@proxy.example", "socks5://user:" + "s" * 256 + "@proxy.example",
])
def test_invalid_proxy_is_rejected_without_disclosing_value(url):
    with pytest.raises(ValueError) as caught:
        telegram_opener(url)
    rendered = "".join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    assert url not in rendered
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
def test_authenticated_proxy_preserves_https_remote_dns_and_post(monkeypatch, scheme):
    # Exercise the real urllib and HTTPSConnection path, replacing only TCP/TLS
    # I/O. This also checks that ambient HTTP proxies cannot hijack the route.
    monkeypatch.setenv("https_proxy", "http://unrelated.example:9999")
    monkeypatch.setenv("NO_PROXY", "*")
    wire = Mock()
    body = b'{"ok":true,"result":{"id":123}}'
    wire.makefile.return_value = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    dial = Mock(return_value=wire)
    monkeypatch.setattr(socks, "create_connection", dial)
    direct = Mock(side_effect=AssertionError("Unexpected direct connection"))
    monkeypatch.setattr(socket, "create_connection", direct)
    tls = []

    def wrap(context, sock, *, server_hostname, **kwargs):
        tls.append((context.verify_mode, context.check_hostname, server_hostname))
        return sock

    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    client = bot.TelegramClient("test-token", proxy_url=scheme + "://user%40name:pass%3Aword@proxy.example:1080")
    assert client.call("getMe", {}, timeout=19) == {"id": 123}
    assert dial.call_args.args[:2] == (("api.telegram.org", 443), 19)
    assert dial.call_args.kwargs == {
        "proxy_type": socks.SOCKS5, "proxy_addr": "proxy.example", "proxy_port": 1080,
        "proxy_rdns": True, "proxy_username": "user@name", "proxy_password": "pass:word",
    }
    assert tls == [(ssl.CERT_REQUIRED, True, "api.telegram.org")]
    sent = b"".join(call.args[0] for call in wire.sendall.call_args_list)
    assert b"POST /bottest-token/getMe HTTP/1.1" in sent
    assert b"pass:word" not in sent
    assert b"Proxy-Authorization" not in sent
    direct.assert_not_called()


def test_certificate_failure_is_not_bypassed_and_socket_is_closed(monkeypatch):
    wire = Mock()
    monkeypatch.setattr(socks, "create_connection", Mock(return_value=wire))
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", Mock(side_effect=ssl.SSLCertVerificationError()))
    with pytest.raises(bot.TelegramAPIError) as caught:
        bot.TelegramClient("test-token", proxy_url=PROXY).call("getMe", {})
    assert caught.value.code == 0
    wire.close.assert_called()


@pytest.mark.parametrize("error_type", [socks.SOCKS5AuthError, socks.ProxyConnectionError, TimeoutError])
def test_proxy_failures_never_fall_back_or_expose_secrets(monkeypatch, error_type):
    monkeypatch.setattr(socks, "create_connection", Mock(side_effect=error_type("test-password test-token")))
    direct = Mock(side_effect=AssertionError("Unexpected direct connection"))
    monkeypatch.setattr(socket, "create_connection", direct)
    client = bot.TelegramClient("test-token", proxy_url=PROXY)
    with pytest.raises(bot.TelegramAPIError) as caught:
        client.call("getMe", {})
    rendered = "".join(traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__))
    assert "test-password" not in rendered
    assert "test-token" not in rendered
    assert caught.value.retryable
    direct.assert_not_called()


def test_proxy_without_auth_uses_default_port(monkeypatch):
    dial = Mock(side_effect=TimeoutError())
    monkeypatch.setattr(socks, "create_connection", dial)
    with pytest.raises(bot.TelegramAPIError):
        bot.TelegramClient("test-token", proxy_url="socks5://proxy.example").call("getMe", {})
    assert dial.call_args.kwargs["proxy_port"] == 1080
    assert dial.call_args.kwargs["proxy_username"] is None
    assert dial.call_args.kwargs["proxy_password"] is None


@pytest.mark.parametrize("entrypoint", [bot, set_menu])
def test_both_entrypoints_use_env_proxy(monkeypatch, entrypoint):
    monkeypatch.setenv("TELEGRAM_PROXY_URL", PROXY)
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("WEBAPP_URL", "https://arena.example.com")
    assert Settings.from_env().telegram_proxy_url == PROXY
    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(entrypoint, "TelegramClient", factory)
    if entrypoint is bot:
        client.call.side_effect = KeyboardInterrupt
    assert entrypoint.main() == 0
    factory.assert_called_once_with("test-token", proxy_url=PROXY)
    if entrypoint is set_menu:
        assert [call.args[0] for call in client.call.call_args_list] == ["setChatMenuButton", "setMyCommands"]
