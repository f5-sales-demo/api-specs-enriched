#!/usr/bin/env bash
# Build deterministic release assets from an exact merged release commit.
set -euo pipefail

: "${VERSION:?VERSION is required}"
: "${RELEASE_COMMIT:?RELEASE_COMMIT is required}"
: "${CANDIDATE_MANIFEST:?CANDIDATE_MANIFEST is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

[ "$(git rev-parse HEAD)" = "$RELEASE_COMMIT" ] || {
  echo "build-release-package: checkout is not the requested release commit" >&2
  exit 1
}

payload="$OUTPUT_DIR"
package="${OUTPUT_DIR}-archive-root"
mkdir -p "$package/domains" "$payload/.handoff" "$payload/docs/specifications/api" \
  "$payload/release" "$payload/release-package"
cp "$CANDIDATE_MANIFEST" "$payload/.handoff/candidate-manifest.json"

python -m scripts.smsv2_release_assets --output-dir "$package" --version "v${VERSION}" --commit "$RELEASE_COMMIT"
cp docs/specifications/api/openapi.json "$package/"
python - "$package/openapi.yaml" <<'PY'
import json
import sys

import yaml

with open("docs/specifications/api/openapi.json", encoding="utf-8") as source:
    specification = json.load(source)
with open(sys.argv[1], "w", encoding="utf-8") as output:
    yaml.safe_dump(specification, output, default_flow_style=False, allow_unicode=True, sort_keys=False)
PY
for spec_file in docs/specifications/api/*.json; do
  name=$(basename "$spec_file")
  case "$name" in
  openapi.json | index.json | minimal-export-defaults.json) continue ;;
  esac
  cp "$spec_file" "$package/domains/"
done
cp docs/specifications/api/index.json "$package/"
cp CHANGELOG.md "$package/"
commit_epoch=$(git show -s --format=%ct "$RELEASE_COMMIT")
release_date=$(date -u -d "@$commit_epoch" +%Y-%m-%d)
upstream_ts=$(jq -r '."x-upstream-timestamp" // "unknown"' docs/specifications/api/index.json)
enriched_version=$(jq -r '."x-enriched-version" // $version' --arg version "$VERSION" docs/specifications/api/index.json)
sed "s/{VERSION}/${upstream_ts}-${enriched_version}/g; s/{DATE}/${release_date}/g" release/README.md >"$package/README.md"

zip_name="f5xc-api-specs-v${VERSION}.zip"
python -m scripts.release_handoff archive --root "$package" --output "$payload/$zip_name" --timestamp "$commit_epoch"

for path in docs/specifications/api/openapi.json docs/specifications/api/index.json docs/specifications/api/minimal-export-defaults.json docs/specifications/api/concurrency_contracts.json docs/specifications/api/smsv2_parity_manifest.json release/api-catalog.json release/upstream-contract-removals.json; do
  mkdir -p "$payload/$(dirname "$path")"
  cp "$path" "$payload/$path"
done

python -m scripts.verify_release_version --version "$VERSION" --tag "v$VERSION" --document docs/specifications/api/openapi.json --document docs/specifications/api/index.json --document docs/specifications/api/concurrency_contracts.json --document docs/specifications/api/smsv2_parity_manifest.json --document release/api-catalog.json --contract-manifest "$package/smsv2-contract-manifest.json" --archive-name "$zip_name"

cp \
  "$package/smsv2-contract.json" \
  "$package/smsv2-evidence-receipt.json" \
  "$package/smsv2-contract-manifest.json" \
  "$payload/release-package/"

printf 'zip_name=%s\n' "$zip_name" >>"$GITHUB_OUTPUT"
