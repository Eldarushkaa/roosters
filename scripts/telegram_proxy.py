"""Per-client SOCKS transport; never change the process-wide socket or opener.

Both socks5 and socks5h resolve the Telegram hostname at the proxy. HTTPS still
uses the standard library's certificate verification and Telegram SNI hostname.
"""

from functools import partial
from http.client import HTTPSConnection
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener, urlopen


def telegram_opener(proxy_url=""):
    """Return a urllib-compatible opener, validating secrets without echoing them.

    An empty setting preserves urllib's normal HTTP(S)_PROXY environment support.
    An explicit SOCKS setting takes precedence over those environment variables.
    """
    if not proxy_url.strip():
        return urlopen
    try:
        if any(character.isspace() for character in proxy_url):
            raise ValueError
        parsed = urlsplit(proxy_url)
        if (parsed.scheme not in {"socks5", "socks5h"} or not parsed.hostname
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError
        port = parsed.port if parsed.port is not None else 1080
        if port < 1:
            raise ValueError
        username = unquote(parsed.username, errors="strict") if parsed.username is not None else None
        password = unquote(parsed.password, errors="strict") if parsed.password is not None else None
        if (username is None) != (password is None):
            raise ValueError
        if username is not None and not all(1 <= len(value.encode("utf-8")) <= 255
                                            for value in (username, password)):
            raise ValueError
    except ValueError:
        raise ValueError("Invalid TELEGRAM_PROXY_URL; expected a SOCKS5 proxy URL.") from None

    try:
        import socks
    except ImportError:
        raise ValueError("SOCKS support requires PySocks; install requirements.txt or rebuild the bot image.") from None

    def connection(host, **kwargs):
        # Replace only this connection's TCP dialer. HTTPSConnection.connect()
        # retains the verified TLS handshake, SNI and socket cleanup behavior.
        client = HTTPSConnection(host, **kwargs)
        client._create_connection = partial(
            socks.create_connection,
            proxy_type=socks.SOCKS5,
            proxy_addr=parsed.hostname,
            proxy_port=port,
            proxy_rdns=True,
            proxy_username=username,
            proxy_password=password,
        )
        return client

    class SocksHTTPSHandler(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(connection, request, context=self._context)

    # Disable ambient proxy handling for this client only: otherwise urllib can
    # change the destination to an HTTP proxy before the SOCKS handler sees it.
    return build_opener(ProxyHandler({}), SocksHTTPSHandler()).open
