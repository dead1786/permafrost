"""Shared test fixtures for Permafrost test suite."""
import json
import os
import shutil
import tempfile
import sys

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test data."""
    d = tempfile.mkdtemp(prefix="pf_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def data_dir(temp_dir):
    """Create a PF-style data directory with all required subdirs."""
    for sub in ["memory/L1", "memory/L2", "memory/L3", "memory/L4",
                "memory/L5", "memory/L6", "acks", "plugins", "agents", "comms"]:
        os.makedirs(os.path.join(temp_dir, sub), exist_ok=True)
    return temp_dir


# Legacy helpers (used by old tests)
def make_temp_dir():
    return tempfile.mkdtemp(prefix="pf_test_")

def cleanup_temp_dir(path):
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)

def write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
