#!/usr/bin/env bash
# Select current protected-main documentation only when it matches the release.
set -euo pipefail

if [[ $# != 3 ]]; then
  echo 'usage: verify-release-docs-ref.sh TAG RELEASE_COMMIT WORKFLOW_COMMIT' >&2
  exit 64
fi
release_tag=$1
release_commit=$2
workflow_commit=$3
[[ "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$workflow_commit" =~ ^[0-9a-f]{40}$ ]] || exit 1
protected_main=$(git rev-parse refs/remotes/origin/main)
[[ "$protected_main" =~ ^[0-9a-f]{40}$ ]] || exit 1
test "$(git rev-parse "$release_tag^{commit}")" = "$release_commit"
git merge-base --is-ancestor "$release_commit" "$protected_main"
git merge-base --is-ancestor "$workflow_commit" "$protected_main"
if ! git diff --quiet "$release_commit" "$protected_main" -- docs release; then
  echo '::error::protected-main documentation differs from the verified release' >&2
  exit 1
fi
# Imported assets are documentation inputs even when stored outside docs/.
if git cat-file -e "$release_commit:docs/_imports" 2>/dev/null; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    source_path=${line%%->*}
    source_path="${source_path#"${source_path%%[![:space:]]*}"}"
    source_path="${source_path%"${source_path##*[![:space:]]}"}"
    [[ -n "$source_path" ]] || continue
    if [[ "$source_path" == /* || "$source_path" == */ ||
      "$source_path" == *//* || "/$source_path/" == */../* ||
      "/$source_path/" == */./* ]]; then
      echo '::error::unsafe documentation import source' >&2
      exit 1
    fi
    if ! git diff --quiet "$release_commit" "$protected_main" -- ":(literal)$source_path"; then
      echo '::error::protected-main imported documentation differs from the release' >&2
      exit 1
    fi
  done < <(git show "$release_commit:docs/_imports")
fi
printf '%s\n' "$protected_main"
