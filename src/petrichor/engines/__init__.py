"""Engines package: drivers and the normalized verdict contract (spec §19.4).

Only this package may produce verdict values (verdict lock, spec §19.4).
This ``__init__`` stays standard-library-only on purpose: the engine images
(``Dockerfile.dummy``) copy the package skeleton and run the dummy server
without any third-party dependency installed.
"""
