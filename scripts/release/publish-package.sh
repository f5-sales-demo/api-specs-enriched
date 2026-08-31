#!/usr/bin/env bash
# Verify the package handoff before publishing an immutable GitHub release.
set -euo pipefail

: "${VERSION:?VERSION is required}"
: "${RELEASE_COMMIT:?RELEASE_COMMIT is required}"
: "${PAYLOAD_DIR:?PAYLOAD_DIR is required}"
: "${HANDOFF_PATH:?HANDOFF_PATH is required}"
: "${ARTIFACT_NAME:?ARTIFACT_NAME is required}"
: "${ARTIFACT_DIGEST:?ARTIFACT_DIGEST is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"

candidate_manifest="$PAYLOAD_DIR/.handoff/candidate-manifest.json"
python -m scripts.release_handoff package-verify --root "$PAYLOAD_DIR" --handoff "$HANDOFF_PATH" --candidate-manifest "$candidate_manifest" --release-commit "$RELEASE_COMMIT" --artifact-name "$ARTIFACT_NAME" --artifact-digest "$ARTIFACT_DIGEST"

git fetch --tags origin main
[ "$(git rev-parse HEAD)" = "$RELEASE_COMMIT" ] || {
  echo "::error::Publisher checkout is not the release commit"
  exit 1
}
[ "$(git rev-list -n 1 "v${VERSION}")" = "$RELEASE_COMMIT" ] || {
  echo "::error::Release tag does not resolve to the handoff commit"
  exit 1
}

zip_name="f5xc-api-specs-v${VERSION}.zip"
asset_paths=(
  "$PAYLOAD_DIR/$zip_name"
  "$PAYLOAD_DIR/docs/specifications/api/openapi.json"
  "$PAYLOAD_DIR/docs/specifications/api/index.json"
  "$PAYLOAD_DIR/docs/specifications/api/minimal-export-defaults.json"
  "$PAYLOAD_DIR/docs/specifications/api/concurrency_contracts.json"
  "$PAYLOAD_DIR/docs/specifications/api/smsv2_parity_manifest.json"
  "$PAYLOAD_DIR/release/api-catalog.json"
  "$PAYLOAD_DIR/release/upstream-contract-removals.json"
  "$PAYLOAD_DIR/release-package/smsv2-contract.json"
  "$PAYLOAD_DIR/release-package/smsv2-evidence-receipt.json"
  "$PAYLOAD_DIR/release-package/smsv2-contract-manifest.json"
)

# Verification above is deliberately complete before the first GitHub write.
if gh release view "v$VERSION" >/dev/null 2>&1; then
  echo "Release v$VERSION already exists, skipping"
  exit 0
fi

notes_file=$(mktemp)
receipt_line=$(mktemp)
receipt_json=$(mktemp)
trap 'rm -f "$notes_file" "$receipt_line" "$receipt_json"' EXIT
bash scripts/release/build-publication-receipt.sh "$VERSION" "$RELEASE_COMMIT" "${asset_paths[@]}" >"$receipt_line"
sed -n 's/^<!-- publication-receipt:\(.*\) -->$/\1/p' "$receipt_line" >"$receipt_json"
jq -e . "$receipt_json" >/dev/null
python -m scripts.verify_release_version --version "$VERSION" --tag "v$VERSION" --receipt "$receipt_json"

{
  cat CHANGELOG.md
  printf '\n'
  cat release/upstream-contract-removals.md
  printf '\n'
  cat "$receipt_line"
} >"$notes_file"

gh release create "v$VERSION" --title "API Specs v$VERSION" --notes-file "$notes_file" "${asset_paths[@]}"
echo "::notice::Created release v$VERSION from verified handoff for $RELEASE_COMMIT"
