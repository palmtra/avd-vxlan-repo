import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "repo"


@pytest.fixture
def fixture_repo(tmp_path):
    """A writable copy of tests/fixtures/repo (two leaf pairs, three services)."""
    target = tmp_path / "repo"
    shutil.copytree(FIXTURE, target)
    return target
