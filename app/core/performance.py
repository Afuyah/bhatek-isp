"""Response compression and caching for the ISP platform.

Two concerns, both dependency-free:

* ``GzipMiddleware`` — compresses text responses on the way out. HTML pages in
  this app run 20-40 KB and compress to roughly a fifth of that, which dominates
  time-to-first-byte on the sort of mobile links ISP customers actually use.
* ``register_performance`` — long-lived caching for immutable static assets and
  ``no-store`` for anything that carries tenant data.

Compression is skipped for streamed responses (``text/event-stream``), so the
real-time endpoints keep working without buffering.
"""

from __future__ import annotations

import gzip
import io

from flask import request

#: Bodies smaller than this cost more to compress than they save.
MIN_SIZE = 500

#: Compressing these rarely helps, and some are already compressed.
SKIP_TYPES = (
    'image/',
    'video/',
    'audio/',
    'application/zip',
    'application/gzip',
    'application/x-font',
    'font/woff2',
    'application/octet-stream',
)

COMPRESSIBLE = (
    'text/',
    'application/json',
    'application/javascript',
    'application/xml',
    'application/rss+xml',
    'image/svg+xml',
)


def _accepts_gzip(environ) -> bool:
    header = environ.get('HTTP_ACCEPT_ENCODING', '')
    return 'gzip' in header.lower()


def _should_compress(content_type: str, length) -> bool:
    if not content_type:
        return False
    content_type = content_type.lower()
    if any(content_type.startswith(t) for t in SKIP_TYPES):
        return False
    if content_type.startswith('text/event-stream'):
        return False
    if not any(content_type.startswith(t) for t in COMPRESSIBLE):
        return False
    return length is None or length >= MIN_SIZE


class GzipMiddleware:
    """Minimal WSGI gzip layer.

    Buffers the response so ``Content-Length`` stays accurate; responses that
    announce themselves as streamed (``text/event-stream``) pass through
    untouched.
    """

    def __init__(self, app, minimum_size: int = MIN_SIZE):
        self.app = app
        self.minimum_size = minimum_size

    def __call__(self, environ, start_response):
        if not _accepts_gzip(environ):
            return self.app(environ, start_response)

        state = {}
        body_parts = []

        def _start(status, headers, exc_info=None):
            state['status'] = status
            state['headers'] = headers
            return lambda chunk: body_parts.append(chunk)

        app_iter = self.app(environ, _start)
        try:
            for chunk in app_iter:
                body_parts.append(chunk)
        finally:
            if hasattr(app_iter, 'close'):
                app_iter.close()

        body = b''.join(body_parts)
        headers = list(state.get('headers', []))
        status = state.get('status', '200 OK')

        content_type = ''
        length = None
        already_encoded = False
        for key, value in headers:
            lowered = key.lower()
            if lowered == 'content-type':
                content_type = value
            elif lowered == 'content-length':
                try:
                    length = int(value)
                except (TypeError, ValueError):
                    length = None
            elif lowered == 'content-encoding':
                already_encoded = True

        if already_encoded or not _should_compress(content_type, length) or len(body) < self.minimum_size:
            start_response(status, headers)
            return [body]

        buffer = io.BytesIO()
        with gzip.GzipFile(mode='wb', fileobj=buffer, compresslevel=6, mtime=0) as gz:
            gz.write(body)
        compressed = buffer.getvalue()

        # Don't ship something bigger than the original.
        if len(compressed) >= len(body):
            start_response(status, headers)
            return [body]

        headers = [(k, v) for k, v in headers if k.lower() != 'content-length']
        headers.append(('Content-Encoding', 'gzip'))
        headers.append(('Content-Length', str(len(compressed))))
        if not any(k.lower() == 'vary' for k, _ in headers):
            headers.append(('Vary', 'Accept-Encoding'))

        start_response(status, headers)
        return [compressed]


def register_performance(app):
    """Static-asset caching + sane defaults, applied app-wide."""
    import os

    app.config.setdefault('SEND_FILE_MAX_AGE_DEFAULT', int(os.environ.get('STATIC_CACHE_SECONDS', 31536000)))
    app.config.setdefault('TEMPLATES_AUTO_RELOAD', False)
    app.config.setdefault('JSON_SORT_KEYS', False)

    @app.after_request
    def _cache_headers(response):
        path = request.path
        if path.startswith('/static/'):
            # Override rather than setdefault: Flask's send_static_file sets its
            # own max-age header, and immutable is the part we actually want.
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        elif path.startswith('/api/'):
            response.headers.setdefault('Cache-Control', 'no-store')
        else:
            response.headers.setdefault('Cache-Control', 'no-cache')
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        return response

    return app
