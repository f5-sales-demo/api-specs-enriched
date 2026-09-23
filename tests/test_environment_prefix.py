import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RETIRED_PREFIX = "F5" + "XC_"
RETIRED_ENVIRONMENT_NAME = re.compile(rf"(?<![A-Z0-9_]){re.escape(RETIRED_PREFIX)}")
CONTRACT_ROOTS = (
    REPOSITORY_ROOT / "Makefile",
    REPOSITORY_ROOT / "config",
    REPOSITORY_ROOT / "docs" / "en",
    REPOSITORY_ROOT / "docs" / "specifications" / "api",
    REPOSITORY_ROOT / "examples",
    REPOSITORY_ROOT / "release",
    REPOSITORY_ROOT / "scripts",
    REPOSITORY_ROOT / "tests",
)
TEXT_SUFFIXES = {".json", ".md", ".mdx", ".py", ".sh", ".yaml", ".yml"}


def contract_files():
    for root in CONTRACT_ROOTS:
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                yield path


def test_retired_environment_prefix_is_absent_from_maintained_contracts():
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for path in contract_files()
        if RETIRED_ENVIRONMENT_NAME.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
