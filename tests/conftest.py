"""Pytest configuration for the OON therapy benchmark test suite.

Inserts the repo root onto ``sys.path`` so the pipeline modules can be imported
by their top-level names regardless of where pytest is invoked from::

    import build_baseline
    import therapy_codes

The repo root is the parent directory of this ``tests/`` package. We insert it
at position 0 so the real modules win over any same-named shadow on the path.
"""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(scope="session")
def repo_root():
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_dir():
    """Absolute path to the committed ``data/`` output directory."""
    return DATA_DIR


@pytest.fixture(scope="session")
def national_csv_path(data_dir):
    return os.path.join(data_dir, "therapy_oon_benchmark_v0_national.csv")


@pytest.fixture(scope="session")
def by_locality_csv_path(data_dir):
    return os.path.join(data_dir, "therapy_oon_benchmark_v0_by_locality.csv")


@pytest.fixture(scope="session")
def benchmark_json_path(data_dir):
    return os.path.join(data_dir, "therapy_oon_benchmark_v0.json")
