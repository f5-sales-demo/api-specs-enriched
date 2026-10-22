"""Build separately qualified GCP operational assets from frozen sanitized inputs."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

from scripts.utils.gcp_contract import (
    validate_gcp_operational_profile,
    validate_gcp_operational_release,
)

if TYPE_CHECKING:
    from pathlib import Path

PROFILE_FILE = "gcp-operational-health-v2.json"
QUALIFICATION_FILE = "gcp-operational-qualification-v2.json"


def build_gcp_operational_assets(source: Path) -> dict[str, bytes]:
    """Require both exact pinned assets; absent configuration enables no profile."""
    if not source.exists():
        return {}
    pin = json.loads((source / "pins.json").read_bytes())
    if not isinstance(pin, dict) or set(pin) != {"contractFingerprint", "qualificationFingerprint"}:
        raise ValueError("Operational publication pins malformed")
    assets = {name: (source / name).read_bytes() for name in (PROFILE_FILE, QUALIFICATION_FILE)}
    profile = json.loads(assets[PROFILE_FILE])
    qualification = json.loads(assets[QUALIFICATION_FILE])
    validate_gcp_operational_profile(profile, pin["contractFingerprint"])
    validate_gcp_operational_release(
        qualification, pin["qualificationFingerprint"], pin["contractFingerprint"]
    )
    allowed_profile = {
        "schemaVersion",
        "scope",
        "maximumAgeMs",
        "minimumRoundIntervalMs",
        "sourceHashes",
        "parserVersions",
        "qualification",
        "inventoryServices",
        "verRunningStates",
        "exclusions",
        "roles",
        "placementCounts",
    }
    allowed_qualification = {
        "schemaVersion",
        "contractFingerprint",
        "bindingFingerprint",
        "healthyRoundFingerprints",
        "evidenceSha256",
        "campaignCleanupSha256",
        "objectsAbsent",
        "emptyDestroy",
        "stableState",
        "repeatedResumeNoSubmission",
        "terraformHaLifecycle",
    }
    if set(profile) - allowed_profile or set(qualification) - allowed_qualification:
        raise ValueError("Operational publication projection fields differ")
    for name, value in ((PROFILE_FILE, profile), (QUALIFICATION_FILE, qualification)):
        # Only this public projection is uploaded; preserve raw evidence outside source.
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if re.search(
            rb'(?i)(?:"(?:password|token|credential|authorization|cookie|secret|rawBody|inspection)"|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?:\d{1,3}\.){3}\d{1,3})',
            canonical,
        ):
            raise ValueError("Operational publication projection contains private evidence")
        # Source inputs may be formatted; published bytes retain the exact canonical pin.
        assets[name] = canonical
        expected = pin[
            "contractFingerprint" if name == PROFILE_FILE else "qualificationFingerprint"
        ]
        if "sha256:" + hashlib.sha256(canonical).hexdigest() != expected:
            raise ValueError("Operational publication asset digest differs")
    return assets
