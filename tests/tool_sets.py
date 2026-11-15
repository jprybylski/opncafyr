"""The tools the source tree ships, for tests that check the registry's full set.

``DEV_ONLY`` tools live under ``src/opncafyr/adapters/`` so the registry finds them in a
development checkout, but the release wheel leaves them out (pyproject.toml, #41).

``REMOVABLE`` tools ship by default but a build may leave them out with their workflow
pack (datum and ``workflows/datafetch``, #83); they count only when present, so the full
suite also passes on such a build (CI job ``without-datafetch``).
"""

from pathlib import Path

ADAPTERS = Path(__file__).resolve().parents[1] / "src" / "opncafyr" / "adapters"
REMOVABLE = frozenset(t for t in ("datum",) if (ADAPTERS / t / "descriptor.yaml").is_file())
PRODUCT_TOOLS = frozenset({"opnca", "deckifyr", "quartifyr", "gitneighbr"}) | REMOVABLE
DEV_ONLY = frozenset({"example_echo"})
ALL_TOOLS = PRODUCT_TOOLS | DEV_ONLY
PRODUCT_WEB_APPS = frozenset({"opnca", "deckifyr", "gitneighbr"})
