#!/usr/bin/env bash
# Create or resume the release PR, wait for its exact merge, and publish its tag.
set -euo pipefail

: "${CANDIDATE_DIR:?CANDIDATE_DIR is required}"
: "${CANDIDATE_MANIFEST_DIGEST:?CANDIDATE_MANIFEST_DIGEST is required}"
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
: "${UPSTREAM_DIGEST:?UPSTREAM_DIGEST is required}"
: "${VERSION:?VERSION is required}"
: "${PIPELINE_FINGERPRINT:?PIPELINE_FINGERPRINT is required}"
: "${BUMP_TYPE:?BUMP_TYPE is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${RELEASE_STATUS_TOKEN:?RELEASE_STATUS_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

manifest="${CANDIDATE_DIR}/.handoff/candidate-manifest.json"
python -m scripts.release_handoff candidate-verify --root "$CANDIDATE_DIR" --manifest "$manifest" --manifest-digest "$CANDIDATE_MANIFEST_DIGEST" --source-commit "$SOURCE_COMMIT" --upstream-digest "$UPSTREAM_DIGEST" --version "$VERSION" --pipeline-fingerprint "$PIPELINE_FINGERPRINT"
python -m scripts.release_handoff candidate-apply --stage "$CANDIDATE_DIR" --destination .

git config --local user.email "github-actions[bot]@users.noreply.github.com"
git config --local user.name "github-actions[bot]"
git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

branch="release/v${VERSION}"
expected_title="chore: release v${VERSION} (${BUMP_TYPE})"
candidate_digest="sha256:$(sha256sum "$manifest" | awk '{print $1}')"
ownership_marker="<!-- sync-and-enrich-release:v1 source=${SOURCE_COMMIT} candidate=${candidate_digest} -->"

publish_tag() {
  local target_commit=$1
  git fetch --tags origin main
  git merge-base --is-ancestor "$target_commit" origin/main || {
    echo "::error::Release PR merge commit is not reachable from main"
    exit 1
  }
  if git rev-parse -q --verify "refs/tags/v${VERSION}" >/dev/null; then
    tag_commit=$(git rev-parse "v${VERSION}^{}")
    [ "$tag_commit" = "$target_commit" ] || {
      echo "::error::Existing v${VERSION} tag names a different commit"
      exit 1
    }
  else
    git tag -a "v${VERSION}" "$target_commit" -m "Release v${VERSION} (${BUMP_TYPE})"
    git push origin "v${VERSION}"
  fi
  printf 'target_commit=%s\n' "$target_commit" >>"$GITHUB_OUTPUT"
  echo "::notice::Released v${VERSION} from immutable merge ${target_commit}"
}

mapfile -t matches < <(
  gh pr list --state all --head "$branch" --json number,state,mergeCommit,headRefName,baseRefName,title,body,isCrossRepository --jq '.[] | @base64'
)
if [ "${#matches[@]}" -gt 1 ]; then
  echo "::error::Multiple release PRs claim ${branch}; refusing ambiguous ownership"
  exit 1
fi

if [ "${#matches[@]}" -eq 1 ]; then
  existing=$(printf '%s' "${matches[0]}" | base64 --decode)
  release_pr=$(jq -r '.number' <<<"$existing")
  release_state=$(jq -r '.state' <<<"$existing")
  jq -e --arg branch "$branch" --arg title "$expected_title" --arg marker "$ownership_marker" '.headRefName == $branch and .baseRefName == "main" and .title == $title and
     (.isCrossRepository == false) and (.body | contains($marker))' <<<"$existing" >/dev/null || {
    echo "::error::Release PR #${release_pr} is foreign or has unexpected ownership metadata"
    exit 1
  }
  case "$release_state" in
  MERGED)
    target_commit=$(jq -er '.mergeCommit.oid' <<<"$existing")
    publish_tag "$target_commit"
    ;;
  OPEN)
    auto_merge=$(gh pr view "$release_pr" --json autoMergeRequest --jq 'if .autoMergeRequest then "true" else "false" end')
    if [ "$auto_merge" != true ]; then
      gh pr merge "$release_pr" --squash --auto --delete-branch
    fi
    WAIT_FOR_MERGE_MAX_TOTAL=7200 bash scripts/release/wait-for-merge.sh "$release_pr"
    target_commit=$(gh pr view "$release_pr" --json state,mergeCommit --jq 'if .state == "MERGED" then .mergeCommit.oid else empty end')
    [ -n "$target_commit" ] || {
      echo "::error::Release PR did not produce a merge commit"
      exit 1
    }
    publish_tag "$target_commit"
    ;;
  CLOSED)
    echo "::error::Release PR #${release_pr} closed without merging; refusing to supersede it"
    exit 1
    ;;
  *)
    echo "::error::Unexpected release PR state: ${release_state}"
    exit 1
    ;;
  esac
else
  git checkout -b "$branch"
  git add -A -- CHANGELOG.md .github_release release/api-catalog.json release/upstream-contract-removals.json release/upstream-contract-removals.md docs/specifications/api docs/api-reference
  if git diff --staged --quiet; then
    echo "::error::Release detector requested v${VERSION}, but no generated output is staged"
    exit 1
  fi
  git commit -m "$expected_title"
  git push origin "$branch"
  pr_url=$(gh pr create --base main --head "$branch" --title "$expected_title" --body "${ownership_marker}
Automated release v${VERSION} (${BUMP_TYPE}). Created by the sync-and-enrich workflow.")
  release_pr=$(gh pr view "$branch" --json number --jq '.number')
  pr_head_sha=$(gh pr view "$release_pr" --json headRefOid --jq '.headRefOid')
  GH_TOKEN="$RELEASE_STATUS_TOKEN" gh api --method POST "repos/${GITHUB_REPOSITORY}/statuses/${pr_head_sha}" -f state=success -f context='Check linked issues' -f description='Automated release branch is exempt'
  echo "Created release PR: ${pr_url}"
  gh pr merge "$release_pr" --squash --auto --delete-branch
  WAIT_FOR_MERGE_MAX_TOTAL=7200 bash scripts/release/wait-for-merge.sh "$release_pr"
  target_commit=$(gh pr view "$release_pr" --json state,mergeCommit --jq 'if .state == "MERGED" then .mergeCommit.oid else empty end')
  [ -n "$target_commit" ] || {
    echo "::error::Release PR did not produce a merge commit"
    exit 1
  }
  publish_tag "$target_commit"
fi
