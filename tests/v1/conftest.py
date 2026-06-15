"""Pytest configuration for the v1 (``oon_bench``) test package.

Inserts the repo root onto ``sys.path`` so the v1 pipeline package and the
shared scope module import by their top-level names regardless of where pytest
is invoked from::

    import oon_bench
    import oon_bench.schemas
    import therapy_codes

The repo root is the parent of the ``tests/`` directory (i.e. two levels up
from this file: ``tests/v1/conftest.py`` -> ``tests/`` -> repo root). We insert
it at position 0 so the real modules win over any same-named shadow on the path.

This mirrors the top-level ``tests/conftest.py`` (which serves the v0 suite) so
the v1 package is self-sufficient: running ``pytest tests/v1`` alone resolves
``oon_bench`` even if the top-level conftest is not collected.
"""
import os
import sys

import pytest

# tests/v1/conftest.py -> tests/v1 -> tests -> repo root
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
DATA_DIR = os.path.join(REPO_ROOT, "data")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(scope="session")
def repo_root():
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_dir():
    """Absolute path to the committed ``data/`` directory (v0 + v1 outputs)."""
    return DATA_DIR


@pytest.fixture(scope="session")
def v0_by_locality_csv_path(data_dir):
    """Path to the committed v0 by-locality CSV (the v1 fallback layer)."""
    return os.path.join(data_dir, "therapy_oon_benchmark_v0_by_locality.csv")
