"""What this launcher runs in, for the ``tools`` block of every manifest (#42).

In cloud container mode the launch script passes the image ID (``docker image inspect
--format '{{.Id}}'``, the config digest). Unlike a registry digest, the image ID is the
same whether the image was pulled from a registry or loaded from an archive
(``docker load``), so a run made in either can name the same image (#43).
"""

from __future__ import annotations

import re

from opncafyr import __version__

IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_state: dict[str, str | None] = {"container_image": None}


def set_container_image(image_id: str | None) -> None:
    if image_id is not None and not IMAGE_ID_RE.match(image_id):
        raise ValueError("a container image ID looks like sha256:<64 hex digits>")
    _state["container_image"] = image_id


def container_image() -> str | None:
    return _state["container_image"]


def base_tools() -> dict[str, str]:
    """``{"opncafyr": <version>}``, plus ``container_image`` in container mode."""
    tools = {"opncafyr": __version__}
    image = container_image()
    if image is not None:
        tools["container_image"] = image
    return tools
