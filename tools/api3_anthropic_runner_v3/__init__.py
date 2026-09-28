"""Fenced-JSON compatibility namespace backed by the shared generator."""
from pathlib import Path
__path__.append(str(Path(__file__).resolve().parent.parent / "api3_anthropic_runner_v2"))
