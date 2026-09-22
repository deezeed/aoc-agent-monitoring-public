"""Tests _load_json_file, extracted straight from monitor.py. Shared
open+json.load+except-return-default boilerplate that used to be
independently repeated 5 times (known-CC-versions, webhook settings,
notify settings, project-dir lookup, status.json) before being factored
out. Runs against scratch files, never anything real."""
import sys, os, json, tempfile, shutil

sys.path.insert(0, os.path.dirname(__file__))
from lib.extract import exec_functions
from lib.check import Checker

ns = exec_functions(["_load_json_file"], {"json": json})
_load_json_file = ns["_load_json_file"]

c = Checker()

SCRATCH = tempfile.mkdtemp(prefix="aoc_test_load_json_file_")

c.check("missing file -> default (None if unspecified)", _load_json_file(os.path.join(SCRATCH, "nope.json")) is None)
c.check("missing file -> custom default", _load_json_file(os.path.join(SCRATCH, "nope.json"), default={"x": 1}) == {"x": 1})

corrupt_path = os.path.join(SCRATCH, "corrupt.json")
with open(corrupt_path, "w", encoding="utf-8") as f:
    f.write("{not valid json,,,")
c.check("corrupt JSON -> default, doesn't raise", _load_json_file(corrupt_path, default=[]) == [])

valid_path = os.path.join(SCRATCH, "valid.json")
with open(valid_path, "w", encoding="utf-8") as f:
    json.dump({"a": 1, "b": [1, 2, 3]}, f)
c.check("valid JSON -> parsed value returned as-is", _load_json_file(valid_path) == {"a": 1, "b": [1, 2, 3]})

empty_path = os.path.join(SCRATCH, "empty.json")
with open(empty_path, "w", encoding="utf-8") as f:
    pass  # zero-byte file -- not valid JSON at all
c.check("empty file -> default, doesn't raise", _load_json_file(empty_path, default="fallback") == "fallback")

shutil.rmtree(SCRATCH, ignore_errors=True)
c.finish()
