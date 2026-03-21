"""
Shared test fixtures for Permafrost test suite.
"""

import json
import os
import shutil
import tempfile
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_temp_dir():
    """Create a temporary directory for test data."""
    return tempfile.mkdtemp(prefix="pf_test_")


def cleanup_temp_dir(path):
    """Remove temporary directory."""
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


def write_json(path, data):
    """Write JSON data to a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json(path):
    """Read JSON data from a file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
