"""Shared helpers for the API test suite."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

HTTP_OK_STATUS_CODE = 200
"""The status code of a successfully served asset."""


def fetch_ui_bundle(client: httpx.Client) -> str:
    """Fetch the built JS bundle referenced by the served /ui/ shell (ADR-0101)."""
    html = client.get("/ui/").text
    match = re.search(r'src="(/ui/assets/[^"]+\.js)"', html)
    if match is None:
        raise ValueError("the index.html shell must reference the built bundle")
    response = client.get(match.group(1))
    if response.status_code != HTTP_OK_STATUS_CODE:
        raise ValueError(f"the bundle was not served: status {response.status_code}")
    if not response.headers["content-type"].startswith("text/javascript"):
        raise ValueError(f"the bundle content type is wrong: {response.headers['content-type']}")
    return response.text


def fetch_ui_stylesheet(client: httpx.Client) -> str:
    """Fetch the built CSS asset referenced by the served /ui/ shell (ADR-0101)."""
    html = client.get("/ui/").text
    match = re.search(r'href="(/ui/assets/[^"]+\.css)"', html)
    if match is None:
        raise ValueError("the index.html shell must reference the built stylesheet")
    response = client.get(match.group(1))
    if response.status_code != HTTP_OK_STATUS_CODE:
        raise ValueError(f"the stylesheet was not served: status {response.status_code}")
    if not response.headers["content-type"].startswith("text/css"):
        raise ValueError(f"the stylesheet content type is wrong: {response.headers['content-type']}")
    return response.text
