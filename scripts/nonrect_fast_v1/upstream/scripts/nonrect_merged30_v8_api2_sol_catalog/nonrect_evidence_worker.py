"""One-off Blender-side evidence renderer for a NON-RECTANGULAR prepared room.

Runs inside Blender against an already-materialized room blend and emits the
same evidence layer the sealed v8 dataset pre-render produces:
`standardized_{perspective,top,identity_map}.png`,
`collision_geometry_manifest.json` (+ per-object .ply) and
`prepared_render_manifest.json`.

All pixel and geometry work is done by the SEALED v8 release's own functions,
imported unmodified.  The only thing skipped is the pair of rectangle-specific
provenance equality gates in `blender_prepared_worker.main`:

  * `_scene_boundary()` hard-requires `len(boundary) == 4`
  * `_source_architecture_contract()` must equal `metadata.architecture_contract`,
    a 4-canonical-wall shape that cannot express a 12-gon

Neither gate affects rendering; both reject non-rectangular rooms outright.
Object identity, asset identity, geometry fingerprints and the render-state
sanitation checks are all still enforced below.

Usage (from the host, not directly):
  Blender --background --factory-startup --disable-autoexec <room.blend> \
      --python-exit-code 1 --python <this file> -- \
      --normalized-scene-json <scene.json> --out-dir <dir> [--release <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


def parse_args() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--normalized-scene-json", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=768)
    parser.add_argument("--render-engine", default="BLENDER_EEVEE_NEXT")
    parser.add_argument("--cycles-device", default="CPU")
    parser.add_argument("--cycles-samples", type=int, default=16)
    return parser.parse_args(values)


def main() -> None:
    args = parse_args()
    # These workers use flat imports (`from blender_worker import ...`) because
    # Blender runs them with their own directory on sys.path.  Import them the
    # same way: going through the `benchmark.rendering` package instead pulls in
    # `blender.py`, which needs PIL, absent from Blender's bundled Python.
    release_src = Path(args.release).resolve() / "src"
    for relative in ("benchmark/rendering", "benchmark/materialization"):
        sys.path.insert(0, str(release_src / relative))

    from blender_worker import (  # noqa: E402
        _add_lighting,
        _configure_render,
        _render_identity_map,
        _render_views,
        _write_collision_geometry_manifest,
    )
    from blender_prepared_worker import (  # noqa: E402
        ASSET_ID_PROPERTY,
        INSTANCE_ID_PROPERTY,
        _descendants,
        _expected_objects,
        _registered_roots,
        _technically_hidden,
        _validate_instance_geometry,
    )
    from blend_inspector_worker import (  # noqa: E402
        _validate_sanitized_render_state,
    )

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = Path(args.normalized_scene_json).expanduser().resolve()
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))

    source_path = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    if source_path is None:
        raise RuntimeError("worker was not given a source blend")
    pre_existing = sorted(
        obj.name for obj in bpy.data.objects if obj.type in {"CAMERA", "LIGHT"}
    )
    if pre_existing:
        raise RuntimeError(
            f"prepared blend contains non-ephemeral cameras or lights: {pre_existing}"
        )
    render_state = _validate_sanitized_render_state()
    if not render_state["passed"]:
        raise RuntimeError(
            "prepared blend has non-canonical render state: "
            + json.dumps(render_state["mismatches"], sort_keys=True)
        )

    expected_objects = _expected_objects(normalized)
    roots = _registered_roots()
    if set(roots) != set(expected_objects):
        raise RuntimeError(
            "blend identity set differs from normalized scene: "
            f"blend={sorted(roots)!r} normalized={sorted(expected_objects)!r}"
        )

    objects = []
    registered: set = set()
    for evaluator_id in sorted(expected_objects):
        expected = expected_objects[evaluator_id]
        root = roots[evaluator_id]
        expected_asset_id = str(
            ((expected.get("asset_ref") or {}).get("asset_key"))
            or expected.get("jid")
            or ""
        )
        observed_asset_id = str(root.get(ASSET_ID_PROPERTY) or "")
        if not expected_asset_id or observed_asset_id != expected_asset_id:
            raise RuntimeError(f"asset identity mismatch for {evaluator_id!r}")
        descendants = {root, *_descendants(root)}
        meshes = [obj for obj in descendants if obj.type == "MESH"]
        if not meshes:
            raise RuntimeError(f"instance {evaluator_id!r} has no renderable mesh")
        if any(_technically_hidden(obj) for obj in descendants):
            raise RuntimeError(f"instance {evaluator_id!r} is hidden or disabled")
        if registered & descendants:
            raise RuntimeError(f"instance {evaluator_id!r} overlaps another hierarchy")
        registered.update(descendants)
        _validate_instance_geometry(
            evaluator_id=evaluator_id,
            root=root,
            meshes=meshes,
            expected=expected,
        )
        objects.append({
            "id": evaluator_id,
            "instance_id": str(root.get(INSTANCE_ID_PROPERTY) or ""),
            "asset_id": observed_asset_id,
            "root_object_name": root.name,
            "root_object": root.name,
            "mesh_object_names": sorted(obj.name for obj in meshes),
            "representation": "asset_mesh",
            "mesh_path": None,
            "canonical_center": list(expected.get("center") or []),
            "canonical_size": list(expected.get("size") or []),
            "canonical_rotation_degrees": list(expected.get("rotation") or []),
            "rendered_bounds_center": list(expected.get("center") or []),
            "vertical_anchor": None,
            "vertical_anchor_source": "fixed_catalog_no_anchor",
        })

    # The polygon boundary comes from the normalized scene, which is what the
    # room was materialized from.  `_scene_boundary()` is skipped only because it
    # rejects anything other than 4 vertices.
    boundary = [[float(x), float(y)] for x, y in normalized["boundary"]]
    scene_height = float(normalized["scene_height"])

    render_config = _configure_render(
        args.width,
        args.height,
        args.render_engine,
        cycles_device=args.cycles_device,
        cycles_samples=args.cycles_samples,
        cycles_denoising=False,
    )
    _add_lighting(boundary, scene_height)
    # No physical walls are activated for this room, so no wall-id naming (the
    # rectangle-only path) is reached.
    views = _render_views(boundary, scene_height, out_dir, active_wall_ids=[])
    standardized_camera_policy = next(
        view["camera_policy"] for view in views if view.get("name") == "perspective"
    )
    canonical_ids = sorted(expected_objects)
    identity_view, identity_legend, identity_palette = _render_identity_map(
        canonical_ids, out_dir
    )
    views.append(identity_view)

    geometry_manifest_path = _write_collision_geometry_manifest(out_dir, objects)

    manifest = {
        "backend": "nonrect_prepared_scene_read_only_oneoff_v1",
        "blender_version": bpy.app.version_string,
        "blend_file": source_path.as_posix(),
        "normalized_scene_path": normalized_path.as_posix(),
        "source_scene_saved": False,
        "scene_mutation_scope": "ephemeral_benchmark_camera_and_lighting_only",
        "render_engine": args.render_engine,
        "render_config": render_config,
        "views": views,
        "standardized_camera_policy": standardized_camera_policy,
        "identity_legend": identity_legend,
        "identity_palette": identity_palette,
        "identity_render": {
            "status": "available",
            "camera_source": "standardized_perspective",
            "camera_policy": standardized_camera_policy["policy_id"],
            "architecture_identity": "neutral_background",
            "canonical_object_count": len(canonical_ids),
            "scene_mutated": False,
            "color_encoding": "raw_linear_rgb_8bit",
        },
        "objects": objects,
        "collision_geometry_manifest": (
            str(geometry_manifest_path) if geometry_manifest_path is not None else None
        ),
        "room_boundary_vertex_count": len(boundary),
        "non_rectangular_room": True,
        "skipped_rectangle_only_gates": [
            "blender_prepared_worker._scene_boundary (requires len(boundary)==4)",
            "blender_prepared_worker architecture_contract equality "
            "(4-canonical-wall contract cannot express a polygon room)",
        ],
        "renderer_source_release": str(Path(args.release).resolve()),
    }
    (out_dir / "prepared_render_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "evidence_rendered",
        "out_dir": str(out_dir),
        "objects": len(objects),
        "views": [view.get("name") for view in views],
        "boundary_vertices": len(boundary),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
