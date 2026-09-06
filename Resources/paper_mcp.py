"""Forward the app-owned viewer credential in memory to its existing MCP client."""
import os
import httpx

original = httpx.AsyncClient


class AuthenticatedClient(original):
    def __init__(self, *args, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        token = os.environ.get('KIMI_PAPER_TOKEN')
        if token:
            headers['Authorization'] = 'Bearer ' + token
        kwargs['headers'] = headers
        kwargs['follow_redirects'] = False
        super().__init__(*args, **kwargs)

    async def send(self, request, *args, **kwargs):
        if request.url.host != '127.0.0.1' or str(request.url.port) != os.environ.get('KIMI_PAPER_PORT'):
            request.headers.pop('Authorization', None)
        return await super().send(request, *args, **kwargs)


if __name__ == '__main__':
    httpx.AsyncClient = AuthenticatedClient
    from tex_mcp_web.cli import main_mcp
    raise SystemExit(main_mcp())
