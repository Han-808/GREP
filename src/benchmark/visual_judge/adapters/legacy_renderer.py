"""Compatibility import for :mod:`benchmark.visual_judge.adapters.provider_renderer`."""
import sys
from benchmark.visual_judge.adapters import provider_renderer as _implementation
sys.modules[__name__] = _implementation
