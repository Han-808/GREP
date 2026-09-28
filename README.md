# Layout_DDD

Layout_DDD generates and evaluates 3D scene layouts. Core APIs live in
`src/benchmark`; workflows, contracts and operator guidance are in `docs`.

## Current implementation

`layout-ddd-evaluate` / `benchmark.api.evaluation.run_evaluate` owns ordinary
scene evaluation. Model experiments use the unified runner, with explicit
open-space, multi-room or non-rectangular input mode:

```bash
python3 scripts/run_uniform_model_evaluation.py --help
```

Current judge/provider adapters share one implementation. Old `legacy_*` import
paths remain compatibility aliases. Generation also shares one two-stage core;
v2/v3 entrypoints select strict or fenced JSON without duplicating the engine.
See [current and legacy implementations](docs/current_and_legacy_implementations.md)
for the exact entrypoints, retained feature differences and source lineage.

## Registered historical baselines

The [floor-plan baseline registry](configs/runners/floorplan_evaluator_baselines_v1.json)
records designated reference runs. It does not make every snapshot the latest
package implementation. The repository selector describes and verifies the
registered source without starting evaluation:

```bash
python3 scripts/run_floorplan_evaluator.py --mode single_room
python3 scripts/run_floorplan_evaluator.py --mode multi_room
python3 scripts/run_floorplan_evaluator.py --mode non_rectangular_multi_room
```

Single-room selects the [published frozen evaluator](evaluator_snapshots/single_room_sceneweaver_20260909_v1/README.md).
Nonrect's historical recipe requires its pinned local releases; rectangular
multi-room has a historical baseline record but no recovered full source.
Missing historical source is never replaced with current code.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest tests/test_floorplan_evaluator_publication.py
```

Real evaluation additionally requires the selected workflow's assets, prepared
inputs, Blender and private model credentials. Never commit credentials, raw API
exchanges or generated scene/evaluation artifacts. Publishing source does not
recompute historical scores or change any already running evaluation.
