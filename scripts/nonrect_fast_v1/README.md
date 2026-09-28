# Non-rectangular evaluation sidecars

These scripts preserve the campaign-specific sidecars and their runtime dependencies.
They are opt-in local experiment tools, not the default benchmark evaluator or a
portable campaign preset. Importing the modules does not start evaluation.

The sidecars require the original campaign plans, prepared rooms, migration records,
Blender installation, and immutable evaluator/materializer releases referenced in
`source_pins.json`. Several campaign paths intentionally retain their historical local
locations. Do not substitute the current checkout for a missing pinned release.
`derive.py` checks source hashes before building a separately identified runtime; the
vendored files in `upstream/` retain their pinned bytes.

Live entry points require `--run`; preparation and evaluation use separate ownership
locks. Credentials are supplied at runtime and are not included here. The Blender
wrapper shares admission slots through `NONRECT_BLENDER_SLOT_DIR` and
`NONRECT_BLENDER_SLOTS`; invoking the wrapper itself starts Blender.

The content-fingerprint policy defaults to strict validation. The explicit trusted
input mode records skipped checks and does not fabricate fingerprints or promote a
new baseline. No existing experiment was launched, resumed, or reconfigured as part
of integrating these files into main.

Offline checks:

```sh
PYTHONPATH=src:.:scripts python -m pytest \
  tests/test_nonrect_fast_pipeline.py tests/test_nonrect_fast_handoff.py
```

`test_nonrect_fast_fingerprints.py` additionally reads the pinned local release
sources and is marked `requires_local_data`; missing sources are skipped.
