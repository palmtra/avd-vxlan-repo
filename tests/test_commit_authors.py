#!/usr/bin/env python3
"""The commit-authors check (.github/scripts/check_commit_authors.py)."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "check_commit_authors.py"
_spec = importlib.util.spec_from_file_location("check_commit_authors", _SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

ALLOWED = {"owner@example.com"}


class CommitAuthors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self._git("init", "-q")
        self.base = self._commit("base", "Owner", "owner@example.com")

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args, env=None):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True, env=env).stdout.strip()

    def _commit(self, message, name, email):
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        self._git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self._git("rev-parse", "HEAD")

    def _bad(self):
        return [subject for _, _, subject in check.uncredited("%s..HEAD" % self.base, ALLOWED, cwd=self.repo)]

    def test_listed_author_passes(self):
        self._commit("feat: by owner", "Owner", "Owner@Example.com")
        self.assertEqual(self._bad(), [])

    def test_listed_co_author_passes(self):
        self._commit("feat: by claude\n\nCo-authored-by: Owner <owner@example.com>", "Claude", "noreply@anthropic.com")
        self.assertEqual(self._bad(), [])

    def test_claude_alone_fails(self):
        self._commit("feat: claude only\n\nCo-Authored-By: Claude <noreply@anthropic.com>", "Claude", "noreply@anthropic.com")
        self.assertEqual(self._bad(), ["feat: claude only"])

    def test_only_the_range_is_checked(self):
        # Commits before the range (old history) are not judged.
        self._git("reset", "-q", "--hard", self.base)
        unlisted = self._commit("old: someone else", "Other", "other@example.com")
        self.assertEqual(check.uncredited("%s..HEAD" % unlisted, ALLOWED, cwd=self.repo), [])

    def test_authors_file_ignores_comments_and_case(self):
        path = Path(self.repo) / "authors"
        path.write_text("# people\nOwner@Example.com  # main\n\n")
        self.assertEqual(check.load_authors(path), {"owner@example.com"})


if __name__ == "__main__":
    unittest.main()
