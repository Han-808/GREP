#!/usr/bin/env python3
"""Compatibility entrypoint for the shared two-stage generator (single_json_code_fence_v1)."""
from pathlib import Path
import importlib.util
import sys

RUNNER_VERSION = "2.0.0"
RUN_MANIFEST_SCHEMA_VERSION = "hy34_two_stage_run_manifest_v3"
CASE_RESULT_SCHEMA_VERSION = "hy34_case_result_v2"
SHARED_CORE_ROOT = '../api3_anthropic_runner_v2'
JSON_EMISSION_POLICY = 'single_json_code_fence_v1'

_entry = Path(__file__).resolve()
_root = (_entry.parent / SHARED_CORE_ROOT).resolve()
_spec = importlib.util.spec_from_file_location("_two_stage_core_loader", _root / "_core_loader.py")
_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader)
_core = _loader.load_core(__name__, _root, JSON_EMISSION_POLICY, _entry)
if __name__ == "__main__":
    raise SystemExit(_core.main())
sys.modules[__name__] = _core
