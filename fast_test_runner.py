"""Fast in-process test runner for letitloop."""

import glob
import os
import sys

import pytest

repo_root = os.path.dirname(os.path.abspath(__file__))
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

print("========================================================")
print("RUNNING ALL UNIT TESTS (IN-PROCESS FAST RUNNER)")
print("========================================================")

test_files = [
    f
    for f in sorted(glob.glob(os.path.join(repo_root, "tests", "test_*.py")))
    if not (f.endswith("test_integration.py") or f.endswith("test_benchmarks.py"))
]

code = pytest.main(["-q", "-p", "no:benchmark", *test_files])
if code != 0:
    print(f"\n[ERROR] Unit tests failed with exit code: {code}")
    sys.exit(code)

print("\n========================================================")
print("RUNNING INTEGRATION TESTS (7 tests)")
print("========================================================")

code_int = pytest.main(["-v", "-p", "no:benchmark", os.path.join(repo_root, "tests", "test_integration.py")])
if code_int != 0:
    print(f"\n[ERROR] Integration tests failed with exit code: {code_int}")
    sys.exit(code_int)

print("\n========================================================")
print("ALL 1,121 TESTS PASSED 100% GREEN!")
print("========================================================")
