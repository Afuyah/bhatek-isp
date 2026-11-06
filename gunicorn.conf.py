"""Gunicorn configuration.

Tuned for a mixed workload: short JSON/web requests plus a small number of
long-lived server-sent-event streams (payment status, dashboards).

Threads matter here. A sync worker is blocked for the whole life of an SSE
stream, so with N workers you can only hold N streams. ``gthread`` lets each
worker serve several streams while still handling normal requests.

Override anything with the matching environment variable, e.g.
``WEB_CONCURRENCY=8 gunicorn -c gunicorn.conf.py wsgi:app``.
"""

import multiprocessing
import os

# ---------------------------------------------------------------- networking
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8080")
backlog = 2048
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

# ---------------------------------------------------------------- workers
# Threads rather than processes: cheaper, and required for concurrent SSE.
workers = int(os.environ.get("WEB_CONCURRENCY", min(4, multiprocessing.cpu_count() * 2 + 1)))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gthread")
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
worker_connections = 1000

# SSE streams run for up to 120s, so the worker timeout must be well above it.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 30
# Recycle workers to bound memory growth; jitter avoids mass restarts.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = 200

# ---------------------------------------------------------------- lifecycle
preload_app = True
# Give in-flight SSE streams a moment to finish before the worker is killed.
graceful_shutdown = True

# ---------------------------------------------------------------- logging
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(b)s %(L)ss "%(a)s"'

# Trust proxy headers only when running behind a load balancer.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")

# ---------------------------------------------------------------- hooks
def on_starting(server):
    server.log.info("starting bhatek-isp with %s %s workers x %s threads",
                    workers, worker_class, threads)
