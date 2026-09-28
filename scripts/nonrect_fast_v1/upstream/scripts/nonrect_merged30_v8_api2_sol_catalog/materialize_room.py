#!/usr/bin/env python3
"""Materialize one non-rectangular room into an evaluation-ready .blend.

Verified end to end on 2026-09-19 for gpt-5.6-sol/scene_011568/room_006:
152.9s, inspection_report.json status=passed, 93 MB room_evaluation.blend.

The non-rectangular evaluator lives only in the separate compat checkout, so
this script inserts that checkout's src on sys.path the same way
scripts/prepare_nonrect_selected10_recovery7_bundles.py does.

Contract notes learned the hard way:
  - asset ids come from selected_asset.jid, not asset_id
  - the frozen catalog paths are hardcoded in scripts/evaluate_missing67_r*/offline.py
  - source_identity needs layout_id, room_id, and path+sha256 per artifact

The materialization revision this produces is v1. The historical campaign
plan.json claims v4, which does not exist anywhere in the compat checkout's
src, so results here are NOT a reproduction of that campaign.

Usage:
  .venv/bin/python <this script> --model gpt-5.6-sol \
      --scene scene_011568 --room room_006 --dest <new empty path>

  --list-rooms   show the rooms a scene resolves to, then exit (no Blender)
  --plan-only    build the materialization plan only (no Blender, seconds)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path("/Users/han_mohan/Desktop/Layout_DDD")
COMPAT = Path(
    "/Users/han_mohan/Desktop/Layout_DDD/Support/artifacts/releases/"
    "nonrect_materialization_compat_sol_tolerance_v1_20260924"
)
GENERATION_ROOT = REPO / (
    "Support/artifacts/outputs/e2e_multi_room/"
    "nonrect_selected10_completed_models_v2_r1"
)
ASSET_CSV = REPO / "Support/Assets/imaginarium_asset_info.csv"
ASSET_ROOT = REPO / "Support/Assets/imaginarium_assets"
CATALOG_SNAPSHOT = "imaginarium-shared-agent-db-v1-8ea5e21ef6c710f7"
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")

ARTIFACTS = {
    "room_layout": "room_layout.json",
    "room_program": "room_program.json",
    "object_plan": "stage_a/object_plan.json",
    "asset_selection": "retrieval/asset_selection.json",
    "generated_scene": "generated_scene.json",
    "compiled_architecture": "compiled_architecture.json",
}

sys.path.insert(0, str(COMPAT / "src"))

from benchmark.materialization.catalog import FrozenCatalog  # noqa: E402
from benchmark.non_rectangular.blender_materialization import (  # noqa: E402
    BlenderNonRectangularRoomMaterializer,
)
from benchmark.non_rectangular.materialization import (  # noqa: E402
    build_nonrect_room_materialization_plan,
    materialize_nonrect_room,
)
from benchmark.non_rectangular.preflight import (  # noqa: E402
    NonRectangularEvaluationInput,
    prepare_non_rectangular_evaluation,
)
from benchmark.non_rectangular.room_unit import (  # noqa: E402
    build_room_evaluation_units,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--scene", default="scene_011568")
    parser.add_argument("--room", default="room_006")
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--generation-root", type=Path, default=GENERATION_ROOT)
    parser.add_argument("--blender-bin", type=Path, default=BLENDER)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--list-rooms", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    scene_root = args.generation_root / args.model / args.scene
    if not scene_root.is_dir():
        print(f"scene root does not exist: {scene_root}", file=sys.stderr)
        return 2

    loaded = {}
    for name, rel in ARTIFACTS.items():
        path = scene_root / rel
        if not path.is_file():
            print(f"missing required artifact: {path}", file=sys.stderr)
            return 2
        loaded[name] = json.loads(path.read_text(encoding="utf-8"))

    preflight = prepare_non_rectangular_evaluation(
        NonRectangularEvaluationInput.from_artifacts(
            room_layout=loaded["room_layout"],
            room_program=loaded["room_program"],
            object_plan=loaded["object_plan"],
            generated_scene=loaded["generated_scene"],
        )
    )
    units = build_room_evaluation_units(preflight)

    if args.list_rooms:
        print(f"layout_id: {preflight.layout_id}")
        for unit in units:
            print(f"  {unit.room_id}  objects={unit.generated_object_count}")
        return 0

    matching = [unit for unit in units if unit.room_id == args.room]
    if not matching:
        available = ", ".join(unit.room_id for unit in units)
        print(f"room {args.room} not in this scene. available: {available}",
              file=sys.stderr)
        return 2
    unit = matching[0]

    selection = loaded["asset_selection"]
    asset_ids = {
        obj["selected_asset"]["jid"]
        for room in selection["rooms"]
        for obj in room.get("objects", [])
        if isinstance(obj.get("selected_asset", {}).get("jid"), str)
    }
    catalog = FrozenCatalog(
        asset_csv=ASSET_CSV,
        asset_root=ASSET_ROOT,
        allowed_asset_ids=asset_ids,
        snapshot_id=CATALOG_SNAPSHOT,
    )

    if args.plan_only:
        plan, _canonical, assets, _architecture = (
            build_nonrect_room_materialization_plan(
                unit,
                room_layout=loaded["room_layout"],
                asset_selection=selection,
                catalog=catalog,
                compiled_architecture=loaded["compiled_architecture"],
            )
        )
        print(json.dumps({
            "status": "plan_built",
            "room_id": unit.room_id,
            "instances": len(plan.get("instances", [])),
            "assets_resolved": len(asset_ids),
            "materialization_revision": plan.get("materialization_revision"),
            "catalog_snapshot_id": plan.get("catalog_snapshot_id"),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.dest is None:
        print("--dest is required unless --plan-only or --list-rooms",
              file=sys.stderr)
        return 2
    destination = args.dest.expanduser().resolve()
    if destination.exists():
        print(f"destination must not exist yet: {destination}", file=sys.stderr)
        return 2

    identity = {
        "layout_id": preflight.layout_id,
        "room_id": unit.room_id,
        "artifacts": {
            name: {
                "path": str(scene_root / rel),
                "sha256": sha256_file(scene_root / rel),
            }
            for name, rel in ARTIFACTS.items()
        },
    }

    started = time.time()
    result = materialize_nonrect_room(
        unit,
        destination=destination,
        room_layout=loaded["room_layout"],
        asset_selection=selection,
        source_identity=identity,
        catalog=catalog,
        blender_bin=args.blender_bin,
        backend=BlenderNonRectangularRoomMaterializer(),
        timeout_seconds=args.timeout_seconds,
        compiled_architecture=loaded["compiled_architecture"],
    )
    inspection = json.loads(
        (destination / "inspection_report.json").read_text(encoding="utf-8")
    )
    print(json.dumps({
        "status": "materialized",
        "elapsed_seconds": round(time.time() - started, 1),
        "model": args.model,
        "scene_id": args.scene,
        "room_id": unit.room_id,
        "blend_path": str(result.blend_path),
        "inspection_status": inspection.get("status"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
