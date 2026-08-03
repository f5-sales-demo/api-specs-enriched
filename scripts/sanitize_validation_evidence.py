# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Remove live-validation secrets and tenant URLs from report artifacts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

_REDACTION = "[REDACTED]"


def sensitive_values_from_environment() -> set[str]:
    """Return non-empty credential and tenant URL forms used by validation."""
    token = os.environ.get("F5XC_API_TOKEN", "").strip()
    api_url = os.environ.get("F5XC_API_URL", "").strip()
    values = {value for value in (token, api_url) if value}

    if api_url:
        parsed = urlsplit(api_url)
        values.update(value for value in (parsed.netloc, parsed.hostname) if value)
        hostname = parsed.hostname or ""
        console_suffix = ".console.ves.volterra.io"
        if hostname.endswith(console_suffix):
            tenant = hostname.removesuffix(console_suffix)
            if tenant:
                values.add(tenant)

    return values


def sanitize_file(source: Path, destination: Path, sensitive_values: set[str]) -> None:
    """Write one sanitized UTF-8 evidence file without modifying its source."""
    content = source.read_text(encoding="utf-8")
    for value in sorted(sensitive_values, key=len, reverse=True):
        content = content.replace(value, _REDACTION)
    destination.write_text(content, encoding="utf-8")


def sanitize_evidence(
    required_paths: list[Path],
    optional_paths: list[Path],
    output_dir: Path,
    sensitive_values: set[str],
) -> None:
    """Atomically publish sanitized copies after every source is readable."""
    if output_dir.exists():
        raise FileExistsError(f"sanitized evidence output already exists: {output_dir}")

    sources = [*required_paths, *(path for path in optional_paths if path.exists())]
    names = [path.name for path in sources]
    if len(names) != len(set(names)):
        raise ValueError("validation evidence source names must be unique")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".validation-evidence-", dir=output_dir.parent) as temporary:
        staged = Path(temporary) / "sanitized"
        staged.mkdir()
        for source in sources:
            sanitize_file(source, staged / source.name, sensitive_values)
        staged.rename(output_dir)


def main() -> int:
    """Sanitize each requested validation evidence file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--required", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()

    sanitize_evidence(
        args.required,
        args.paths,
        args.output_dir,
        sensitive_values_from_environment(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
