#!/usr/bin/env bash
# Warns when docs/ + CLAUDE.md have fallen behind real code changes.
#
# Root cause this guards against (see the 2026-08-31 documentation audit,
# reconciled into CLAUDE.md/docs/*.md): the practice of updating docs in the
# same pass as the code change silently lapsed for ~22 days/21 commits before
# anyone noticed, because nothing ever actually checked. Cheap, no false
# positives by construction (it only ever reads real commit history), meant to
# be run at the start (or end) of a work session, not wired into any commit
# hook that could block real work.
#
# MEASURES COVERAGE, NOT RECENCY (rewritten after the codebase health audit).
# The first version compared the LATEST commit touching docs against the
# LATEST touching code. That has a structural blind spot: any single
# doc-touching commit resets it. The audit found the check reporting "OK"
# while NINE undocumented feature commits sat behind it -- including a whole
# new supply type, Save/Load Project, Extract Diagram, and a Cell-Type revert
# that left CLAUDE.md actively describing classes the code no longer had.
# It was reporting green on exactly the lapse it existed to catch.
#
# So: count the commits that touched frontend/ or backend/ and touched NO
# documentation, since the last commit that DID touch documentation. That is
# the quantity that actually went wrong.
#
# Usage: scripts/check_doc_drift.sh [threshold_commits]
#   threshold_commits defaults to 3.

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

THRESHOLD="${1:-3}"

# The most recent commit that touched documentation -- the last "doc sweep".
last_doc_commit=$(git log -1 --format=%H -- CLAUDE.md docs/ 2>/dev/null || true)

if [ -z "$last_doc_commit" ]; then
  echo "check_doc_drift: no documentation commits found -- skipping."
  exit 0
fi

# Every commit since then that touched real code. A commit is "undocumented"
# when it changed frontend/ or backend/ and changed nothing under docs/ or
# CLAUDE.md -- i.e. it shipped behaviour without recording it anywhere.
undocumented=()
while IFS= read -r sha; do
  [ -z "$sha" ] && continue
  files=$(git show --name-only --format= "$sha")
  echo "$files" | grep -qE '^(frontend|backend)/' || continue
  echo "$files" | grep -qE '^(CLAUDE\.md|docs/)' && continue
  undocumented+=("$sha")
done < <(git log --format=%H "${last_doc_commit}..HEAD")

count=${#undocumented[@]}

if [ "$count" -eq 0 ]; then
  echo "check_doc_drift: every code commit since the last doc sweep also touched docs. OK."
  exit 0
fi

if [ "$count" -ge "$THRESHOLD" ]; then
  echo "check_doc_drift: WARNING -- $count code commit(s) since the last doc sweep touched no documentation:"
  for sha in "${undocumented[@]}"; do
    git log -1 --format='    %h %ad  %s' --date=short "$sha"
  done
  echo
  echo "  Fold anything doc-worthy into CLAUDE.md/docs/ before the gap grows further."
  echo "  (Reporting OK here while these piled up is exactly how the 2026-08-31"
  echo "   lapse recurred -- the old check compared timestamps, not coverage.)"
  exit 1
fi

echo "check_doc_drift: $count code commit(s) since the last doc sweep touched no documentation"
echo "  (under the ${THRESHOLD}-commit threshold). OK."
exit 0
