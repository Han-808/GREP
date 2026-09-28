"""Compatibility import for :mod:`benchmark.visual_judge.adapters.provider_camera`."""
import sys
from benchmark.visual_judge.adapters import provider_camera as _implementation
sys.modules[__name__] = _implementation
