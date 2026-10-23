#!/bin/bash
# Claude Code cloud sessions commit as the repository owner, with Claude
# credited through the Co-Authored-By trailer. Without this the session's
# default identity (Claude <noreply@anthropic.com>) is the only author, and
# .github/workflows/commit-authors.yml rejects the commit.
# Local sessions keep the developer's own git identity.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$(dirname "$0")/../.."
git config user.name "Austin Nwaka"
git config user.email "stynhaq@live.com"
