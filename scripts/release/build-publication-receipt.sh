#!/usr/bin/env bash
# Emit the publication receipt for one release.
#
# usage: build-publication-receipt.sh <version> <commit> <asset-path>...
#
# Writes a single line to stdout:
#
#   <!-- publication-receipt:{"assets":{...},"commit":"<40-hex>","version":"x.y.z"} -->
#
# The line goes into the release body, where it binds the exact SHA-256 of every
# published asset to the commit the tag resolves to. Consumers verify what they
# downloaded against it instead of trusting that a release named vX.Y.Z contains
# what vX.Y.Z contained the last time they looked.
#
# terraform-provider-xcsh refuses to consume a release without one — see its
# download-api-specs action and sync-openapi.yml (#1460). A release published
# without a receipt is undeliverable downstream, so every failure here is fatal
# rather than a warning.
#
# The five-asset set is enforced here, not just downstream: a receipt attesting
# to four assets, or to a bundle named for a different version, is worse than no
# receipt because it looks authoritative while covering the wrong release.
set -euo pipefail

fail() {
  echo "build-publication-receipt: $*" >&2
  exit 1
}

[ "$#" -ge 3 ] || fail "usage: build-publication-receipt.sh <version> <commit> <asset-path>..."

version=$1
commit=$2
shift 2

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
  fail "version must be MAJOR.MINOR.PATCH with no leading v, got: ${version}"

# Lowercase hex only: consumers test against ^[0-9a-f]{40}$, so an uppercase or
# abbreviated SHA would be rejected there, after the release is already immutable.
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] ||
  fail "commit must be a full lowercase 40-character Git SHA, got: ${commit}"

digest() {
  # Emitted as "sha256:<hex>", the same form GitHub reports in
  # .assets[].digest. A consumer can then compare the two for equality instead of
  # reassembling a prefix at every call site, and the value is self-describing if
  # the algorithm ever changes.
  #
  # It also keeps the digest out of the way of entropy-based secret scanners.
  # Gitleaks' generic-api-key rule fires on a secret-ish keyword next to a
  # high-entropy value, and asset filenames supply the keyword: measured on the
  # bare-hex form, "api-catalog.json" and "openapi.json" were reported while
  # "index.json" was not. Prefixed, the rule does not fire at all, so no consumer
  # needs an allowlist to commit this data.
  local hex
  if command -v sha256sum >/dev/null 2>&1; then
    hex=$(sha256sum "$1" | awk '{print $1}')
  else
    hex=$(shasum -a 256 "$1" | awk '{print $1}')
  fi
  printf 'sha256:%s' "$hex"
}

assets='{}'
for path in "$@"; do
  [ -f "$path" ] || fail "asset does not exist: ${path}"
  name=$(basename "$path")
  # Two paths with the same basename would silently overwrite each other in the
  # receipt, attesting to whichever came last.
  if [ "$(jq -r --arg n "$name" 'has($n)' <<<"$assets")" = "true" ]; then
    fail "duplicate asset name: ${name}"
  fi
  assets=$(jq -cS --arg n "$name" --arg d "$(digest "$path")" '. + {($n): $d}' <<<"$assets")
done

expected=$(jq -cnS --arg bundle "f5xc-api-specs-v${version}.zip" \
  '["api-catalog.json", $bundle, "index.json", "minimal-export-defaults.json", "openapi.json"] | sort')
actual=$(jq -cS 'keys | sort' <<<"$assets")
if [ "$actual" != "$expected" ]; then
  fail "asset set does not match the five-asset contract for v${version}"$'\n'"  expected: ${expected}"$'\n'"  actual:   ${actual}"
fi

# -c keeps it on one line, which the consumer's line-anchored regex requires;
# -S sorts keys so the same inputs always produce the same bytes.
receipt=$(jq -cnS \
  --argjson assets "$assets" \
  --arg commit "$commit" \
  --arg version "$version" \
  '{assets: $assets, commit: $commit, version: $version}')

printf '<!-- publication-receipt:%s -->\n' "$receipt"
