"""Per-candidate HTTPS CONNECT gateway for an existing user-configured proxy.

The agent cannot reach the upstream proxy directly. This gateway accepts only
explicit Codex service authorities and never forwards client-supplied headers.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import os
import re
import secrets
import socket
import ssl
from urllib.parse import unquote, urlsplit


# OAuth refresh is deliberately excluded: a read-only candidate must not rotate
# tokens remotely and then fail to persist the replacement.
CODEX_HOSTS = frozenset({'chatgpt.com', 'api.openai.com'})
HEADER_LIMIT = 16384


class ProxyError(Exception):
    """Public error without network configuration or authentication material."""


def codex_authority(value: str) -> str:
    match = re.fullmatch(r'([a-zA-Z0-9.-]+):443', value, flags=re.ASCII)
    if match is None or match[1].lower() not in CODEX_HOSTS:
        raise ProxyError('只允许已知 Codex HTTPS 服务。')
    return match[1].lower() + ':443'


def proxy_configuration(environment=None):
    """Return a validated, in-memory route; never persist or log the result."""
    source = os.environ if environment is None else environment
    raw = next((source[key] for key in ('KIMI_PAPER_UPSTREAM_PROXY', 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy',
                                       'ALL_PROXY', 'all_proxy') if source.get(key)), None)
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.query
                or parsed.fragment or parsed.path not in {'', '/'}
                or not 1 <= port <= 65535 or any(ord(c) < 33 for c in raw)):
            raise ValueError()
        username = unquote(parsed.username or '')
        password = unquote(parsed.password or '')
        if ':' in username or any(ord(c) < 32 for c in username + password):
            raise ValueError()
        authorization = None
        if parsed.username is not None:
            authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        return parsed.scheme, parsed.hostname, port, authorization
    except (ValueError, TypeError):
        raise ProxyError('系统代理配置不受支持，请使用有效的 HTTP 或 HTTPS 代理。') from None


class CodexProxy:
    """Ephemeral authenticated gateway; construct with ``await start()``."""

    def __init__(self, route):
        self._route = route
        self._authorization = 'Basic ' + base64.b64encode(('candidate:' + secrets.token_urlsafe(32)).encode()).decode()
        self._server = None
        self._ipv6_server = None
        self._tasks = set()
        self._writers = set()
        self._closed = False

    @classmethod
    async def start(cls, environment=None):
        route = proxy_configuration(environment)
        if route is None:
            return None
        gateway = cls(route)
        # Seatbelt's localhost:port filter includes both IP families. Reserve
        # both so another local service cannot occupy the allowed IPv6 port.
        for _ in range(8):
            gateway._server = await asyncio.start_server(gateway._accept, '127.0.0.1', 0, limit=HEADER_LIMIT)
            ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            try:
                ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                ipv6.bind(('::1', gateway.endpoint[1]))
                ipv6.setblocking(False)
                gateway._ipv6_server = await asyncio.start_server(gateway._accept, sock=ipv6, limit=HEADER_LIMIT)
                return gateway
            except OSError:
                ipv6.close()
                gateway._server.close()
                await gateway._server.wait_closed()
            except BaseException:
                ipv6.close()
                await gateway.close()
                raise
        raise ProxyError('无法创建独立候选网络端口。')

    @property
    def endpoint(self):
        """Non-secret exact sandbox exception: (numeric loopback IP, TCP port)."""
        if self._server is None or self._closed:
            raise ProxyError('候选网络连接已关闭。')
        return '127.0.0.1', self._server.sockets[0].getsockname()[1]

    @property
    def environment(self):
        """Ephemeral capability; pass only to its candidate, never log this map."""
        host, port = self.endpoint
        credentials = base64.b64decode(self._authorization[6:]).decode()
        address = f'http://{credentials}@{host}:{port}'
        return {**{key: address for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                            'http_proxy', 'https_proxy', 'all_proxy')},
                'NO_PROXY': '', 'no_proxy': ''}

    async def close(self):
        if self._closed:
            return
        self._closed = True
        servers = [server for server in (self._server, self._ipv6_server) if server is not None]
        for server in servers:
            server.close()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        for writer in list(self._writers):
            writer.close()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2)
            for task in pending:
                task.cancel()
            for task in done:
                if not task.cancelled():
                    task.exception()
        # Python 3.12 waits for accepted connections too: close their writers
        # before awaiting Server.wait_closed(), rather than deadlocking here.
        for server in servers:
            try:
                await asyncio.wait_for(server.wait_closed(), 1)
            except asyncio.TimeoutError:
                pass
        self._authorization = ''
        self._route = None

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        self._writers.add(writer)
        upstream = None
        try:
            if self._closed or len(self._tasks) > 32:
                return
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            if len(header) > HEADER_LIMIT:
                raise ProxyError('请求过大。')
            if self._closed:
                return
            lines = header.decode('ascii').split('\r\n')
            request = lines[0].split(' ')
            if len(request) != 3 or request[0] != 'CONNECT' or request[2] not in {'HTTP/1.0', 'HTTP/1.1'}:
                await self._reject(writer, 405)
                return
            authority = codex_authority(request[1])
            authorizations = []
            for line in lines[1:-2]:
                if not line or line[0].isspace() or ':' not in line:
                    raise ProxyError('无效请求头。')
                name, value = line.split(':', 1)
                if not re.fullmatch(r'[!#$%&\'*+.^_`|~0-9A-Za-z-]+', name):
                    raise ProxyError('无效请求头。')
                if name.lower() in {'content-length', 'transfer-encoding'}:
                    raise ProxyError('CONNECT 不接受请求体。')
                if name.lower() == 'proxy-authorization':
                    authorizations.append(value.strip())
            if len(authorizations) != 1 or not hmac.compare_digest(authorizations[0], self._authorization):
                await self._reject(writer, 407)
                return
            scheme, host, port, authentication = self._route
            options = {'ssl': ssl.create_default_context(), 'server_hostname': host} if scheme == 'https' else {}
            remote_reader, upstream = await asyncio.wait_for(asyncio.open_connection(host, port, limit=HEADER_LIMIT, **options), 15)
            self._writers.add(upstream)
            if self._closed:
                return
            headers = [f'CONNECT {authority} HTTP/1.1', f'Host: {authority}']
            if authentication:
                headers.append('Proxy-Authorization: ' + authentication)
            upstream.write(('\r\n'.join(headers) + '\r\n\r\n').encode('ascii'))
            await upstream.drain()
            response = await asyncio.wait_for(remote_reader.readuntil(b'\r\n\r\n'), 15)
            if self._closed:
                return
            status = response.split(b'\r\n', 1)[0].split(b' ')
            if len(response) > HEADER_LIMIT or len(status) < 2 or status[0] not in {b'HTTP/1.0', b'HTTP/1.1'} or status[1] != b'200':
                await self._reject(writer, 502)
                return
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            if self._closed:
                return
            pipes = [asyncio.create_task(self._relay(reader, upstream)),
                     asyncio.create_task(self._relay(remote_reader, writer))]
            try:
                await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pipe in pipes:
                    pipe.cancel()
                await asyncio.gather(*pipes, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except (ProxyError, OSError, ValueError, UnicodeError, asyncio.TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await self._reject(writer, 502)
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    self._writers.discard(stream)
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 1)
                    except (OSError, asyncio.CancelledError, asyncio.TimeoutError):
                        pass
            self._tasks.discard(task)

    @staticmethod
    async def _reject(writer, status):
        try:
            writer.write(f'HTTP/1.1 {status} Request Rejected\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'.encode())
            await writer.drain()
        except (OSError, ConnectionError):
            pass

    @staticmethod
    async def _relay(reader, writer):
        while data := await asyncio.wait_for(reader.read(65536), 180):
            writer.write(data)
            await writer.drain()
