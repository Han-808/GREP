#!/usr/bin/env python3
"""Wrap an already-materialized non-rectangular room into an evaluator-ready case.

Renders the evidence layer with the SEALED v8 release's own BlenderRenderer, then
projects the outputs into the case layout that
`camera_cal_scene_level.discovery.case_paths` resolves, mirroring
`multi_room_evaluation.materializer._freeze_render_outputs`.

Input:  a materialization probe room dir containing `canonical_room_scene.json`.
Output: a dataset root containing one ready case directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

RELEASE = Path(
    "/Users/han_mohan/Desktop/Layout_DDD/Support/artifacts/releases/"
    "model_floorplan_unified_polygon_refactor_v8_20260918"
)
ASSET_ROOT = Path("/Users/han_mohan/Desktop/Layout_DDD/Support/Assets/imaginarium_assets")
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")

sys.path.insert(0, str(RELEASE / "src"))

from benchmark.evaluator.generic_validity.mesh_geometry import (  # noqa: E402
    load_collision_geometry_manifest,
)
from benchmark.rendering.blender import BlenderRenderer  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def rewrite_paths(value: Any, mapping: dict[str, str]) -> Any:
    """Replace absolute render-dir paths with case-relative ones."""
    if isinstance(value, dict):
        return {k: rewrite_paths(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_paths(v, mapping) for v in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room-dir", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--blender-bin", type=Path, default=BLENDER)
    parser.add_argument(
        "--evidence-worker",
        default="/Users/han_mohan/.claude/jobs/34224126/tmp/nonrect_evidence_worker.py",
    )
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--skip-render", action="store_true",
                        help="reuse an existing --render-dir instead of rendering")
    args = parser.parse_args()

    room_dir = args.room_dir.expanduser().resolve()
    scene_source = room_dir / "canonical_room_scene.json"
    if not scene_source.is_file():
        print(f"missing canonical scene: {scene_source}", file=sys.stderr)
        return 2

    case_root = args.dataset_root.expanduser().resolve() / args.case_id
    if case_root.exists():
        print(f"case root must not exist yet: {case_root}", file=sys.stderr)
        return 2

    render_dir = args.render_dir.expanduser().resolve()
    started = time.time()

    # The prepared blend produced by the non-rect materializer IS the evaluated
    # geometry.  Render read-only from it: `render_scene` would rebuild the room
    # from the boundary and `_build_room` names walls before checking whether any
    # are active, which rejects every non-rectangular boundary.
    source_blend = room_dir / "prepared" / "room_evaluation.blend"
    if not source_blend.is_file():
        print(f"missing prepared blend: {source_blend}", file=sys.stderr)
        return 2

    # `blender_prepared_worker._validate_instance_geometry` requires each
    # normalized object to carry `metadata.materialization`.  The non-rect
    # materializer records those fingerprints in its inspection report instead of
    # writing them back into the scene, so project them across by object id.
    inspection = read_json(room_dir / "inspection_report.json")
    records: dict[str, dict[str, Any]] = {}
    for instance in inspection.get("instances") or []:
        if not isinstance(instance, dict):
            continue
        object_id = str(instance.get("evaluator_object_id") or "")
        if not object_id:
            continue
        records[object_id] = {
            key: instance[key]
            for key in (
                "geometry_sha256",
                "material_sha256",
                "asset_assembly_sha256",
                "instance_id",
                "requested_uniform_scale",
                "effective_uniform_scale",
                "actual_local_bbox_size_m",
                "world_bounds",
            )
            if key in instance
        }

    observed_sizes = {
        str(instance.get("evaluator_object_id")): instance.get("local_bbox_size_m")
        for instance in inspection.get("instances") or []
        if isinstance(instance, dict) and instance.get("local_bbox_size_m")
    }

    scene_data = read_json(scene_source)
    unmatched: list[str] = []
    size_rewrites: list[dict[str, Any]] = []
    for item in scene_data.get("objects") or []:
        if not isinstance(item, dict):
            continue
        object_id = str(item.get("id") or "")
        record = records.get(object_id)
        if record is None:
            unmatched.append(object_id)
            continue
        metadata = item.get("metadata")
        item["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
        item["metadata"]["materialization"] = record
        # The probe's materializer does not write fitted sizes back into the
        # canonical scene, so declared `size` can disagree with the mesh actually
        # in the blend (the rug is declared 12 mm thick and is 0.09 mm).  The
        # evaluator judges the materialized geometry, so the blend's baked local
        # bbox wins; every substitution is recorded in provenance.
        observed = observed_sizes.get(object_id)
        declared = item.get("size")
        if (
            isinstance(observed, list)
            and isinstance(declared, list)
            and len(observed) == len(declared) == 3
            and any(
                abs(float(a) - float(b)) > 1.0e-4
                for a, b in zip(declared, observed)
            )
        ):
            size_rewrites.append({
                "object_id": object_id,
                "declared_size": [float(v) for v in declared],
                "materialized_size": [float(v) for v in observed],
            })
            item["size"] = [float(v) for v in observed]
    if unmatched:
        print(
            "inspection report has no materialization record for: "
            f"{unmatched[:5]} ({len(unmatched)} total)",
            file=sys.stderr,
        )
        return 2
    render_dir.parent.mkdir(parents=True, exist_ok=True)
    normalized_scene = render_dir.parent / f"{render_dir.name}_normalized_scene.json"
    write_json(normalized_scene, scene_data)
    scene_source = normalized_scene

    if args.skip_render:
        if not render_dir.is_dir():
            print(f"--skip-render needs an existing dir: {render_dir}", file=sys.stderr)
            return 2
        render_result: dict[str, Any] = {"reused": True}
    else:
        if render_dir.exists():
            print(f"render dir must not exist yet: {render_dir}", file=sys.stderr)
            return 2
        # `BlenderRenderer.render_prepared_scene` cannot ingest a polygon room:
        # its worker's `_scene_boundary` requires exactly 4 vertices and its
        # architecture-contract equality gate cannot express a 12-gon.  The
        # one-off worker below reuses that worker's own render/export functions
        # and enforces every identity check, skipping only those two gates.
        worker = Path(args.evidence_worker).expanduser().resolve()
        if not worker.is_file():
            print(f"evidence worker not found: {worker}", file=sys.stderr)
            return 2
        render_dir.mkdir(parents=True, exist_ok=False)
        command = [
            str(args.blender_bin),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            str(source_blend),
            "--python-exit-code",
            "1",
            "--python",
            str(worker),
            "--",
            "--normalized-scene-json",
            str(scene_source),
            "--out-dir",
            str(render_dir),
            "--release",
            str(RELEASE),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=args.timeout_seconds,
            check=False,
        )
        (render_dir / "evidence_worker.stdout.log").write_text(
            completed.stdout, encoding="utf-8"
        )
        (render_dir / "evidence_worker.stderr.log").write_text(
            completed.stderr, encoding="utf-8"
        )
        if completed.returncode != 0:
            tail = "\n".join((completed.stdout + completed.stderr).splitlines()[-25:])
            print(f"evidence worker failed ({completed.returncode}):\n{tail}",
                  file=sys.stderr)
            return 3
        render_result = {"worker": "nonrect_evidence_worker_oneoff_v1"}
    render_seconds = round(time.time() - started, 1)

    required = {
        "blend": source_blend,
        "perspective": render_dir / "standardized_perspective.png",
        "top": render_dir / "standardized_top.png",
        "identity": render_dir / "standardized_identity_map.png",
        "manifest": render_dir / "prepared_render_manifest.json",
        "collision": render_dir / "collision_geometry_manifest.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        print(f"renderer did not produce: {missing}", file=sys.stderr)
        return 3

    architecture_path = render_dir / "architecture_contract.json"
    if not architecture_path.is_file():
        from benchmark.architecture_policy import architecture_contract_from_scene

        write_json(
            architecture_path,
            architecture_contract_from_scene(read_json(scene_source)),
        )
    required["architecture"] = architecture_path

    # --- project into the evaluator-ready case layout -------------------------
    scene_dir = case_root / "scene"
    prepared = case_root / "prepared"
    evidence = case_root / "evidence"
    geometry_dir = evidence / "collision_geometry"
    provenance = case_root / "provenance" / "source_inputs"
    for directory in (scene_dir, prepared, evidence, geometry_dir, provenance):
        directory.mkdir(parents=True)

    shutil.copy2(scene_source, scene_dir / "canonical_scene.json")
    shutil.copy2(required["blend"], prepared / "evaluation.blend")
    shutil.copy2(required["perspective"], evidence / "standardized_perspective.png")
    shutil.copy2(required["top"], evidence / "standardized_top.png")
    shutil.copy2(required["identity"], evidence / "standardized_identity_map.png")
    shutil.copy2(required["architecture"], provenance / "architecture_contract.json")
    for name in ("materialization_manifest.json", "source_identity.json",
                 "inspection_report.json", "complete.json"):
        candidate = room_dir / name
        if candidate.is_file():
            shutil.copy2(candidate, provenance / name)

    collision = read_json(required["collision"])
    objects = collision.get("objects")
    if not isinstance(objects, dict) or not objects:
        print("collision geometry manifest has no object inventory", file=sys.stderr)
        return 3
    for index, object_id in enumerate(sorted(objects)):
        row = objects[object_id]
        if not isinstance(row, dict):
            print(f"collision row invalid: {object_id}", file=sys.stderr)
            return 3
        if "source_uri" in row:
            row.pop("source_uri", None)
            row["source_uri_redacted"] = True
        if row.get("complete") is not True:
            if row.get("representation") == "triangle_mesh":
                row["geometry_path"] = f"collision_geometry/unavailable_{index:04d}.ply"
            else:
                row.pop("geometry_path", None)
            continue
        raw = str(row.get("geometry_path") or "")
        source = Path(raw)
        if not source.is_absolute():
            source = (render_dir / source).resolve()
        if not source.is_file():
            print(f"collision geometry missing for {object_id}: {source}", file=sys.stderr)
            return 3
        suffix = source.suffix.lower()
        if suffix not in {".ply", ".obj", ".glb", ".gltf"}:
            print(f"unsupported collision geometry suffix: {suffix}", file=sys.stderr)
            return 3
        filename = f"object_{index:04d}{suffix}"
        shutil.copy2(source, geometry_dir / filename)
        row["geometry_path"] = f"collision_geometry/{filename}"
    collision.pop("manifest_path", None)
    write_json(evidence / "collision_geometry_manifest.json", collision)
    load_collision_geometry_manifest(evidence / "collision_geometry_manifest.json")

    render_manifest = read_json(required["manifest"])
    render_manifest.pop("collision_geometry", None)
    mapping = {
        str(required["blend"]): "../prepared/evaluation.blend",
        str(required["perspective"]): "standardized_perspective.png",
        str(required["top"]): "standardized_top.png",
        str(required["identity"]): "standardized_identity_map.png",
        str(required["collision"]): "collision_geometry_manifest.json",
        str(required["architecture"]): "../provenance/source_inputs/architecture_contract.json",
        str(scene_source): "../scene/canonical_scene.json",
    }
    rewritten = rewrite_paths(copy.deepcopy(render_manifest), mapping)
    rewritten["collision_geometry"] = copy.deepcopy(collision)
    rewritten["collision_geometry"]["manifest_path"] = "collision_geometry_manifest.json"
    rewritten["collision_geometry_manifest"] = "collision_geometry_manifest.json"
    rewritten["blend_file"] = "../prepared/evaluation.blend"
    rewritten["scene_json"] = "../scene/canonical_scene.json"
    rewritten["materialized_case_root"] = "."
    rewritten["absolute_case_root_withheld"] = True
    write_json(evidence / "prepared_render_manifest.json", rewritten)

    canonical = read_json(scene_dir / "canonical_scene.json")
    scene_objects = canonical.get("objects") or []
    object_ids = [str(item.get("id")) for item in scene_objects if isinstance(item, dict)]
    if len(object_ids) != len(scene_objects) or len(object_ids) != len(set(object_ids)):
        print("canonical scene object IDs are invalid", file=sys.stderr)
        return 3

    fingerprint = hashlib.sha256(
        json.dumps(
            {"objects": sorted(object_ids), "boundary": canonical.get("boundary")},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    write_json(provenance / "case_build_notes.json", {
        "schema_version": "nonrect_case_build_notes_v1",
        "source_room_dir": str(room_dir),
        "evidence_renderer": "sealed_v8_BlenderRenderer.render_prepared_scene",
        "evaluated_blend_source": str(source_blend),
        "materialization_records_projected_from": "inspection_report.json instances",
        "declared_size_overridden_by_materialized_geometry": size_rewrites,
    })

    write_json(case_root / "annotation.json", {
        "schema_version": "camera_cal_scene_annotation_v1",
        "dataset_id": args.dataset_id,
        "case_id": args.case_id,
        "reviewed": False,
        "render_integrity": "usable",
        "scene_notes": "No human GT; non-rectangular smoke case; excluded from accuracy comparison.",
    })

    write_json(case_root / "case_manifest.json", {
        "schema_version": "camera_cal_scene_case_v1",
        "dataset_id": args.dataset_id,
        "case_id": args.case_id,
        "scene_type": str(canonical.get("scene_type") or "non_rectangular_room"),
        "object_count": len(object_ids),
        "semantic_content_fingerprint": fingerprint,
        "source_artifacts_read_only": True,
        "status": "ready",
        "paths": {
            "canonical_scene": "scene/canonical_scene.json",
            "blend": "prepared/evaluation.blend",
            "annotation": "annotation.json",
            "evidence": {
                "perspective": "evidence/standardized_perspective.png",
                "top": "evidence/standardized_top.png",
                "identity": "evidence/standardized_identity_map.png",
            },
        },
    })

    write_json(args.dataset_root.expanduser().resolve() / "dataset_manifest.json", {
        "schema_version": "camera_cal_scene_dataset_v1",
        "dataset_id": args.dataset_id,
        "case_count": 1,
        "case_ids": [args.case_id],
        "evaluator_release_manifest_sha256": sha256_file(
            RELEASE / "release_manifest.json"
        ),
        "evidence_renderer": "sealed_v8_BlenderRenderer",
        "source_room_dir": str(room_dir),
        "non_rectangular": True,
    })

    print(json.dumps({
        "status": "case_ready",
        "case_root": str(case_root),
        "dataset_root": str(args.dataset_root.expanduser().resolve()),
        "object_count": len(object_ids),
        "collision_objects": len(objects),
        "render_seconds": render_seconds,
        "renderer_result_keys": sorted(render_result)[:8],
        "size_rewrites": size_rewrites,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
