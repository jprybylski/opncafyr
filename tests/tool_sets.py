"""The tools the source tree ships, for tests that check the registry's full set.

``DEV_ONLY`` tools live under ``src/opncafyr/adapters/`` so the registry finds them in a
development checkout, but the release wheel leaves them out (pyproject.toml, #41).
"""

PRODUCT_TOOLS = frozenset({"opnca", "deckifyr", "quartifyr", "gitneighbr"})
DEV_ONLY = frozenset({"example_echo"})
ALL_TOOLS = PRODUCT_TOOLS | DEV_ONLY
PRODUCT_WEB_APPS = frozenset({"opnca", "deckifyr", "gitneighbr"})
