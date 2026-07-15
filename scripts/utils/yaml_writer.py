"""Shared YAML writer producing yamllint-compliant output.

PyYAML's default dump does not indent block sequences under their mapping key
and omits the document-start marker, both of which the repo's yamllint config
flags. This helper forces sequence indentation and emits ``---`` so generated
YAML passes the ``Lint Code Base`` gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path


class _IndentDumper(yaml.Dumper):
    """Dumper that indents block sequences under their parent key."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:  # noqa: ARG002
        return super().increase_indent(flow, False)


def dump_yaml(data: Any, *, sort_keys: bool = True) -> str:
    """Serialize ``data`` to a yamllint-compliant YAML string (with ``---``)."""
    body = yaml.dump(
        data,
        Dumper=_IndentDumper,
        default_flow_style=False,
        sort_keys=sort_keys,
        indent=2,
    )
    return "---\n" + body


def write_yaml(data: Any, path: Path, *, header: str = "", sort_keys: bool = True) -> None:
    """Write ``data`` to ``path`` as yamllint-compliant YAML.

    ``header`` (if given) is inserted as leading comment lines after the ``---``
    document-start marker.
    """
    body = yaml.dump(
        data,
        Dumper=_IndentDumper,
        default_flow_style=False,
        sort_keys=sort_keys,
        indent=2,
    )
    with path.open("w") as f:
        f.write("---\n")
        if header:
            f.write(header if header.endswith("\n") else header + "\n")
        f.write(body)
