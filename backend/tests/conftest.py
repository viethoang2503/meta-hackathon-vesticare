"""
conftest.py — shared pytest fixtures.

Auto-patches external services so tests never call real APIs.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_telegram():
    """Prevent tests from sending real Telegram messages.

    Yields the MagicMock so individual tests can inspect call_count / call_args
    when needed (via the pytest fixture injection mechanism).
    """
    mock = MagicMock(return_value=(True, None))
    with patch("app.api.sensor.send_fall_alert", mock):
        yield mock


def registered_route_paths(app) -> set:
    """Every URL path `app` actually serves.

    `{r.path for r in app.routes}` stopped working at Starlette 1.6: each
    `include_router()` call now contributes an `_IncludedRouter` wrapper with no
    `.path` of its own, holding the real routes under `.original_router`. The old
    expression raised `AttributeError`, and once that was worked around it still
    found only `/docs`, `/openapi.json` and `/api/health` -- so these assertions
    would have started passing vacuously if they had been written to tolerate a
    missing attribute.

    Built from `app.openapi()`, which is FastAPI's own public answer to "what does
    this app serve", plus a walk over the route tree for anything openapi omits
    (WebSocket endpoints are not in the schema). That way the assertions describe
    the real URL surface instead of one version's internal route-list shape.
    """

    paths = set(app.openapi().get("paths", {}))

    pending = list(getattr(app, "routes", ()))
    seen = set()
    while pending:
        route = pending.pop()
        if id(route) in seen:
            continue
        seen.add(id(route))
        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            paths.add(path)
        for attribute in ("routes", "original_router"):
            nested = getattr(route, attribute, None)
            if nested is None:
                continue
            pending.extend(nested if isinstance(nested, (list, tuple)) else [nested])
    return paths
