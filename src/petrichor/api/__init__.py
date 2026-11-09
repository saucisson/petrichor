"""HTTP API package (infrastructure layer): the WSGI app of the Gérer service."""

from petrichor.api.app import WSGIApp, create_app

__all__ = ["WSGIApp", "create_app"]
