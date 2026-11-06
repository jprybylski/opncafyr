"""The per-user installer (#19) and offline bundles (#36).

Everything in this package runs under the bare, pinned python-build-standalone
interpreter before any environment exists, so it imports only the standard library,
other ``opncafyr.installer`` modules and ``opncafyr.core.redaction``
(``tests/test_boundaries.py`` checks this). The thin wrappers in ``installer/`` fetch and
verify that interpreter, then run ``run_installer.py``, which calls ``cli.main``.

One code path for every source: the pinned files are fetched (online, from a mirror) or
found (``--from-bundle``, ``--assets``), every SHA-256 is checked against
``runtime/pins.toml`` and the files it points to, and only then is anything staged.
"""
