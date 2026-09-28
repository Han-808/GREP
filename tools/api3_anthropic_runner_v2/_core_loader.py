"""Load the shared generator in an isolated policy-specific module namespace."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys


def load_core(name: str, root: Path, policy: str, entrypoint: Path):
    if policy not in {"strict_json", "single_json_code_fence_v1"}:
        raise ValueError("Unsupported JSON emission policy")
    if name == "__main__":
        package = "_two_stage_cli_" + hashlib.sha256(str(entrypoint).encode()).hexdigest()[:16]
        spec = importlib.util.spec_from_file_location(
            package, root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
        name = package + ".generation_runner"
    spec = importlib.util.spec_from_file_location(name, root / "generation_core.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load shared generation core")
    module = importlib.util.module_from_spec(spec)
    module.JSON_EMISSION_POLICY = policy
    module.COMPATIBILITY_ENTRYPOINT = entrypoint
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module
