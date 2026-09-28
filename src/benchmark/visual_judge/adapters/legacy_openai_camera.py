"""Compatibility import for :mod:`benchmark.visual_judge.adapters.openai_camera`."""
import sys
from benchmark.visual_judge.adapters import openai_camera as _implementation
sys.modules[__name__] = _implementation
