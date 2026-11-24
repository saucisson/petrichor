"""Shared helpers for the API test suite."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


def fetch_ui_bundle(client: httpx.Client) -> str:
    """Fetch the built JS bundle referenced by the served /ui/ shell (ADR-0101)."""
    html = client.get("/ui/").text
    match = re.search(r'src="(/ui/assets/[^"]+\.js)"', html)
    assert match is not None, "the index.html shell must reference the built bundle"
    response = client.get(match.group(1))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    return response.text


def fetch_ui_stylesheet(client: httpx.Client) -> str:
    """Fetch the built CSS asset referenced by the served /ui/ shell (ADR-0101)."""
    html = client.get("/ui/").text
    match = re.search(r'href="(/ui/assets/[^"]+\.css)"', html)
    assert match is not None, "the index.html shell must reference the built stylesheet"
    response = client.get(match.group(1))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    return response.text
