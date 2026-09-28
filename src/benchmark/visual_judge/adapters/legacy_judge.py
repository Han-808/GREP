"""Compatibility import for :mod:`benchmark.visual_judge.adapters.provider_judge`."""
import sys
from benchmark.visual_judge.adapters import provider_judge as _implementation
sys.modules[__name__] = _implementation
