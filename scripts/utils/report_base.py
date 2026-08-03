# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Base reporter class for all documentation generators.

Provides abstract base class and common utilities for generating both
markdown and JSON reports consistently across all generators.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from path_config import PathConfig

logger = logging.getLogger(__name__)


class BaseReporter(ABC):
    """Abstract base class for all report generators.

    Provides:
    - Metadata standardization
    - Markdown table and section generation
    - Dual format support (markdown + JSON)
    - Common path management via PathConfig
    """

    def __init__(
        self,
        title: str,
        description: str,
        path_config: PathConfig | None = None,
    ) -> None:
        """Initialize the reporter.

        Args:
            title: Report title for display
            description: Report description
            path_config: Optional PathConfig instance (creates new if not provided)
        """
        self.title = title
        self.description = description
        self.path_config = path_config or PathConfig()
        self.generated_at = datetime.now(tz=UTC).isoformat()

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary format.

        Returns:
            Dictionary representation of report data
        """

    @abstractmethod
    def to_markdown(self) -> str:
        """Convert report to markdown format.

        Returns:
            Markdown-formatted string
        """

    def generate_all(self, markdown_path: Path, json_path: Path) -> None:
        """Generate both markdown and JSON reports.

        Args:
            markdown_path: Where to write markdown report
            json_path: Where to write JSON report
        """
        self.generate_markdown(markdown_path)
        self.generate_json(json_path)
        logger.info("Generated reports: %s and %s", markdown_path, json_path)

    def generate_markdown(self, path: Path) -> None:
        """Write markdown report to file.

        Args:
            path: File path to write to
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            f.write(self.to_markdown())
        logger.info("Generated markdown report: %s", path)

    def generate_json(self, path: Path) -> None:
        """Write JSON report to file.

        Args:
            path: File path to write to
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Generated JSON report: %s", path)

    # Markdown helpers
    @staticmethod
    def markdown_section(title: str, content: str, level: int = 2) -> str:
        """Create a markdown section with heading and content.

        Args:
            title: Section title
            content: Section content
            level: Heading level (2 = ##, 3 = ###, etc)

        Returns:
            Formatted markdown section
        """
        heading = "#" * level
        return f"{heading} {title}\n\n{content}\n\n"

    @staticmethod
    def markdown_table(
        headers: list[str],
        rows: list[list[str]],
    ) -> str:
        """Create a markdown table.

        Args:
            headers: Column headers
            rows: List of rows, each row is list of cell values

        Returns:
            Formatted markdown table
        """
        if not headers or not rows:
            return ""

        # Header row
        md = "| " + " | ".join(headers) + " |\n"
        # Separator row
        md += "| " + " | ".join(["---"] * len(headers)) + " |\n"
        # Data rows
        for row in rows:
            md += "| " + " | ".join(str(cell) for cell in row) + " |\n"

        return md + "\n"

    @staticmethod
    def markdown_metadata_section(metadata: dict[str, str], level: int = 3) -> str:
        """Create a markdown section for metadata (timestamps, versions, etc).

        Args:
            metadata: Dictionary of key-value pairs
            level: Heading level

        Returns:
            Formatted markdown metadata section
        """
        content = ""
        for key, value in metadata.items():
            content += f"- **{key}**: {value}\n"

        heading = "#" * level
        return f"{heading} Metadata\n\n{content}\n\n"

    def markdown_report_header(self) -> str:
        """Create standard report header with title, description, and timestamp.

        Returns:
            Formatted markdown header
        """
        md = f"# {self.title}\n\n"
        md += f"{self.description}\n\n"
        md += f"**Generated**: {self.generated_at}\n\n"
        return md

    # Server variables helpers (to be overridden in subclasses)
    def markdown_server_variables_section(self) -> str:
        """Create server variables reference section.

        Override in subclass to provide server variables details.

        Returns:
            Formatted markdown section (empty string if not implemented)
        """
        return ""
