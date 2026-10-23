#!/usr/bin/env python3
"""Fail when a commit in a range credits nobody listed in .github/commit-authors.

A commit is credited by its author email or by a ``Co-authored-by: Name
<email>`` trailer (case-insensitive). Merge commits are skipped: GitHub
authors them as whoever merges.

    check_commit_authors.py <rev-range> [--authors-file PATH]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

TRAILER_RE = re.compile(r"^co-authored-by:.*<([^>]+)>\s*$", re.I | re.M)


def load_authors(path):
    emails = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            emails.add(line)
    return emails


def commits(rev_range, cwd=None):
    """``[(sha, author_email, message)]`` for non-merge commits in the range."""
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=%H%x00%ae%x00%B%x1e", rev_range],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout
    found = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, email, message = record.split("\x00", 2)
            found.append((sha, email.strip().lower(), message))
    return found


def uncredited(rev_range, allowed, cwd=None):
    """Commits whose author and co-authors are all outside *allowed*."""
    bad = []
    for sha, email, message in commits(rev_range, cwd):
        credited = {email} | {item.strip().lower() for item in TRAILER_RE.findall(message)}
        if not credited & allowed:
            bad.append((sha, email, message.splitlines()[0] if message else ""))
    return bad


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rev_range")
    parser.add_argument("--authors-file", default=str(Path(__file__).resolve().parent.parent / "commit-authors"))
    args = parser.parse_args(argv)
    allowed = load_authors(args.authors_file)
    bad = uncredited(args.rev_range, allowed)
    for sha, email, subject in bad:
        print("::error::%s (%s) credits nobody in .github/commit-authors: %s" % (sha[:10], email, subject))
    if bad:
        print(
            "Fix: author the commit as a listed person (git config user.email), or add a trailer\n"
            "  Co-authored-by: Name <email>\n"
            "with a listed email, then amend or rebase and push again."
        )
        return 1
    print("All commits in %s credit a listed author." % args.rev_range)
    return 0


if __name__ == "__main__":
    sys.exit(main())
