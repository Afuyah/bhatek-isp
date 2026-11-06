"""Jinja filters available to every template.

Kept out of the app factory so the factory stays a wiring file. They are
defensive by design: templates render values that may be ``None``, a string
from the database, or a real ``datetime``, and a filter must never raise inside
a page render.
"""

from datetime import datetime

from jinja2 import ChainableUndefined
from markupsafe import Markup
import json


def _coerce(value):
    """Return a datetime for ISO strings, or None if it cannot be parsed."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    return value


def datetimeformat(value, format='%b %d, %Y'):
    """Human date, e.g. ``Mar 04, 2026``."""
    if value is None:
        return 'N/A'
    value = _coerce(value)
    if value is None:
        text = str(value) if value is not None else ''
        return text[:19] if len(text) >= 19 else text
    if hasattr(value, 'strftime'):
        return value.strftime(format)
    return str(value)[:19]


def format_date(value, format='%Y-%m-%d'):
    """ISO-ish date, e.g. ``2026-03-04``."""
    if value is None:
        return 'N/A'
    value = _coerce(value)
    if value is None:
        return str(value)[:10]
    if hasattr(value, 'strftime'):
        return value.strftime(format)
    return str(value)[:10]


def format_datetime(value, format='%Y-%m-%d %H:%M'):
    """Date and time without seconds."""
    if value is None:
        return 'N/A'
    value = _coerce(value)
    if value is None:
        return str(value)[:16]
    if hasattr(value, 'strftime'):
        return value.strftime(format)
    return str(value)[:16]


def escapejs(value):
    """Safely embed a Python value inside an inline ``<script>``."""
    if not value:
        return ''
    return Markup(json.dumps(str(value))[1:-1])


FILTERS = {
    'datetimeformat': datetimeformat,
    'format_date': format_date,
    'format_datetime': format_datetime,
    'escapejs': escapejs,
}


def register_template_filters(app):
    """Register every filter in :data:`FILTERS` on ``app``.

    Also switches the template environment to ``ChainableUndefined``. Templates
    in this project are written defensively -- ``{{ stats.total|default(0) }}``
    -- which only works if ``stats.total`` evaluates to an undefined value
    rather than raising. With the default ``Undefined`` the attribute access
    raises before ``default()`` is ever consulted, turning an optional context
    key into a 500. Chainable undefined makes those fallbacks behave as intended;
    tests that want strictness can set ``app.jinja_env.undefined`` themselves.
    """
    app.jinja_env.undefined = ChainableUndefined
    for name, fn in FILTERS.items():
        app.add_template_filter(fn, name)
    return app
