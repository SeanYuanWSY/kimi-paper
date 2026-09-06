import asyncio
import base64
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Resources'))
from paper_proxy import CodexProxy, ProxyError, codex_authority, proxy_configuration


class ProxyValidationTests(unittest.TestCase):
    def test_authority_is_strict(self):
        self.assertEqual(codex_authority('CHATGPT.COM:443'), 'chatgpt.com:443')
        for bad in ('localhost:443', '127.0.0.1:443', '[::1]:443', '192.168.1.1:443',
                    '169.254.169.254:443', 'auth.openai.com:443', 'chatgpt.com:80', 'chatgpt.com.:443',
                    'chatgpt.com.evil.test:443', 'chatgpt.com@127.0.0.1:443',
                    'chatgpt.com:443/path', 'chatgpt.com:443\r\nX: x', 'chatgpt。com:443'):
            with self.subTest(bad=bad), self.assertRaises(ProxyError):
                codex_authority(bad)

    def test_configuration_never_silently_bypasses_invalid_proxy(self):
        self.assertIsNone(proxy_configuration({}))
        for bad in ('socks5://localhost:1080', 'http://localhost:99999', 'http://x/path', 'http://x/?q=1'):
            with self.assertRaises(ProxyError): proxy_configuration({'HTTPS_PROXY': bad})
        self.assertEqual(proxy_configuration({'KIMI_PAPER_UPSTREAM_PROXY': 'https://localhost:8443', 'HTTPS_PROXY': 'http://localhost:99'})[:3], ('https', 'localhost', 8443))
        self.assertEqual(proxy_configuration({'HTTPS_PROXY': 'http://127.0.0.1:7897'})[:3],
                         ('http', '127.0.0.1', 7897))


class ProxyTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.upstream_writers = set()
        async def upstream(reader, writer):
            self.upstream_writers.add(writer)
            try:
                head = await reader.readuntil(b'\r\n\r\n')
                self.received.append(head)
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                while data := await reader.read(1024):
                    writer.write(data); await writer.drain()
            except (asyncio.IncompleteReadError, OSError):
                pass
            finally:
                self.upstream_writers.discard(writer); writer.close()
        self.upstream = await asyncio.start_server(upstream, '127.0.0.1', 0)
        port = self.upstream.sockets[0].getsockname()[1]
        self.gateway = await CodexProxy.start({'HTTPS_PROXY': f'http://upstream:fixture-password@127.0.0.1:{port}'})
        value = urlsplit(self.gateway.environment['HTTPS_PROXY'])
        self.auth = base64.b64encode((value.username + ':' + value.password).encode()).decode()

    async def asyncTearDown(self):
        await self.gateway.close()
        self.upstream.close(); await self.upstream.wait_closed()
        for writer in list(self.upstream_writers): writer.close()

    async def connect(self, authority, auth=None, method='CONNECT', extra=''):
        reader, writer = await asyncio.open_connection(*self.gateway.endpoint)
        writer.write((f'{method} {authority} HTTP/1.1\r\nProxy-Authorization: Basic {auth or self.auth}\r\n' + extra + '\r\n').encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2)
        return reader, writer, response

    async def test_cancel_during_start_releases_ipv4_listener(self):
        real_start = asyncio.start_server
        pending = asyncio.Event()
        first_server = None
        async def delayed(*args, **kwargs):
            nonlocal first_server
            if first_server is None:
                first_server = await real_start(*args, **kwargs)
                return first_server
            pending.set()
            await asyncio.Future()
        with patch('paper_proxy.asyncio.start_server', side_effect=delayed):
            task = asyncio.create_task(CodexProxy.start({'HTTPS_PROXY': 'http://127.0.0.1:7897'}))
            await asyncio.wait_for(pending.wait(), 2)
            endpoint = first_server.sockets[0].getsockname()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            with self.assertRaises(OSError): await asyncio.open_connection(*endpoint)

    async def test_no_proxy_no_listener(self):
        self.assertIsNone(await CodexProxy.start({}))

    async def test_allowed_host_tunnels_and_only_upstream_auth_forwarded(self):
        reader, writer, response = await self.connect('chatgpt.com:443', extra='X-Injected: private\r\n')
        self.assertIn(b' 200 ', response)
        writer.write(b'fixture'); await writer.drain()
        self.assertEqual(await reader.readexactly(7), b'fixture')
        head = self.received[0]
        self.assertIn(b'CONNECT chatgpt.com:443 HTTP/1.1', head)
        self.assertNotIn(self.auth.encode(), head)
        self.assertNotIn(b'X-Injected', head)
        self.assertIn(base64.b64encode(b'upstream:fixture-password'), head)
        writer.close(); await writer.wait_closed()

    async def test_private_targets_never_reach_upstream(self):
        for target in ('127.0.0.1:443', 'localhost:443', '[::1]:443', 'chatgpt.com@127.0.0.1:443',
                       '192.168.1.2:443', 'chatgpt.com:22'):
            reader, writer, response = await self.connect(target)
            self.assertNotIn(b' 200 ', response)
            writer.close(); await writer.wait_closed()
        self.assertEqual(self.received, [])

    async def test_missing_or_other_candidate_capability_rejected(self):
        reader, writer, response = await self.connect('chatgpt.com:443', auth='different-candidate')
        self.assertIn(b' 407 ', response); self.assertEqual(self.received, [])
        writer.close(); await writer.wait_closed()

    async def test_non_connect_and_smuggled_body_rejected(self):
        for method, extra in [('GET', ''), ('CONNECT', 'Content-Length: 5\r\n'),
                              ('CONNECT', 'Transfer-Encoding: chunked\r\n'),
                              ('CONNECT', 'Proxy-Authorization: Basic duplicate\r\n')]:
            reader, writer, response = await self.connect('chatgpt.com:443', method=method, extra=extra)
            self.assertNotIn(b' 200 ', response)
            writer.close(); await writer.wait_closed()
        self.assertEqual(self.received, [])

    async def test_ipv6_same_port_is_reserved_and_authenticated(self):
        host, port = self.gateway.endpoint
        reader, writer = await asyncio.open_connection('::1', port)
        writer.write(b'CONNECT chatgpt.com:443 HTTP/1.1\r\n\r\n')
        await writer.drain()
        self.assertIn(b' 407 ', await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2))
        self.assertEqual(self.received, [])
        writer.close(); await writer.wait_closed()
        occupied = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            occupied.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            with self.assertRaises(OSError): occupied.bind(('::1', port))
        finally:
            occupied.close()

    async def test_close_immediately_after_response_cannot_hang(self):
        for _ in range(20):
            await self.gateway.close()
            port = self.upstream.sockets[0].getsockname()[1]
            self.gateway = await CodexProxy.start({'HTTPS_PROXY': f'http://127.0.0.1:{port}'})
            reader, writer, response = await self.connect('chatgpt.com:443', auth=base64.b64encode(
                (urlsplit(self.gateway.environment['HTTPS_PROXY']).username + ':' +
                 urlsplit(self.gateway.environment['HTTPS_PROXY']).password).encode()).decode())
            await asyncio.wait_for(self.gateway.close(), 3)
            await asyncio.sleep(0)
            self.assertFalse(self.gateway._tasks)
            self.assertFalse(self.gateway._writers)
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b'')
            writer.close(); await writer.wait_closed()

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS Seatbelt test')
    async def test_sandbox_only_reaches_own_gateway_port(self):
        port = self.gateway.endpoint[1]
        upstream_port = self.upstream.sockets[0].getsockname()[1]
        profile = ('(version 1)(deny default)(allow process-exec)(allow file-read*)'
                   '(allow sysctl-read)(allow network-outbound '
                   f'(remote tcp "localhost:{port}"))'
                   '(allow network-outbound (literal "/private/var/run/mDNSResponder"))')
        code = ('import socket; '
                f's=socket.create_connection(("127.0.0.1",{port}),2);s.close(); '
                f's=socket.create_connection(("::1",{port}),2);s.close();'
                '\ntry:\n'
                f' socket.create_connection(("127.0.0.1",{upstream_port}),2)\n'
                'except PermissionError: pass\nelse: raise AssertionError("upstream accessible")')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.sb') as file:
            file.write(profile); file.flush()
            proc = await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec', '-f', file.name,
                sys.executable, '-c', code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            output, error = await asyncio.wait_for(proc.communicate(), 10)
            self.assertEqual(proc.returncode, 0, error.decode())

    async def test_close_terminates_tunnels_and_listener(self):
        endpoint = self.gateway.endpoint
        reader, writer, response = await self.connect('chatgpt.com:443')
        await self.gateway.close()
        self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b'')
        with self.assertRaises(OSError): await asyncio.open_connection(*endpoint)
        writer.close(); await writer.wait_closed()


if __name__ == '__main__': unittest.main()
