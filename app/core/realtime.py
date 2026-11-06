"""Server-sent events for live updates.

The app previously made the browser poll (1 s on the captive portal, 60 s on
the router health page). Each poll is a fresh request through the full
middleware stack — auth, tenant, rate limit, DB session. A single SSE stream
replaces that with one connection that pushes only when something changes.

Usage::

    from app.core.realtime import poll_stream, sse_response

    def fetch():
        return {'status': 'pending'}

    return sse_response(poll_stream(fetch, interval=2, timeout=90,
                                    done=lambda d: d['status'] != 'pending'))

Note for deployment: SSE holds a worker for the life of the stream, so serve
the app with an async worker (gevent, already in requirements) rather than the
sync worker, and disable proxy buffering (``X-Accel-Buffering: no``, below).
"""

from __future__ import annotations

import json
import time

from flask import Response, stream_with_context

#: Heartbeat comment keeps proxies and load balancers from closing an idle stream.
HEARTBEAT_SECONDS = 15


def sse_response(stream, status: int = 200) -> Response:
    """Wrap a generator of ``(event, data)`` pairs in an SSE response."""
    return Response(
        stream_with_context(stream),
        status=status,
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


def format_event(data, event: str = 'update') -> str:
    """Serialise one SSE frame."""
    payload = json.dumps(data, default=str)
    return f'event: {event}\ndata: {payload}\n\n'


def poll_stream(fetch, interval: float = 2.0, timeout: float = 90.0, done=None):
    """Stream the result of ``fetch()`` until ``done(data)`` or timeout.

    ``fetch`` runs inside the request context (and therefore the app context),
    so it may use the ORM. Errors are reported as an ``error`` event instead of
    killing the stream, so the client can show something useful.
    """
    started = time.monotonic()
    last_heartbeat = started
    previous = object()

    while True:
        try:
            data = fetch()
        except Exception as exc:  # surfaced to the client, not swallowed
            yield format_event({'error': str(exc)}, event='error')
            return

        try:
            changed = data != previous
        except Exception:
            changed = True

        if changed:
            yield format_event(data)
            previous = data

        try:
            if done and done(data):
                yield format_event({'final': True}, event='done')
                return
        except Exception:
            pass

        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            yield format_event({'timeout': True}, event='timeout')
            return

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            yield ': keep-alive\n\n'
            last_heartbeat = now

        time.sleep(min(interval, max(0.1, timeout - elapsed)))
