"""Synthetic zero-height floor evidence for isolated object-export fixtures.

These tests do not claim to have observed a real Blender room. Production
collection and rejection of missing architecture evidence are tested separately.
"""
import hashlib
from benchmark.adapters.common.geometry import canonical_room
from benchmark.adapters.scene_weaver.converter import convert_scene_weaver, _select_layout


def synthetic_floor_frame(path, iteration, height=3.0, floor_z=0.0):
    return {
        "schema_version": "sceneweaver_measured_floor_frame_v1",
        "measurement_source": "saved_blend_architecture_world_vertices",
        "layout_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "selected_iteration": iteration,
        "native_blend_sha256": hashlib.sha256(b"synthetic architecture fixture").hexdigest(),
        "floor_z_m": floor_z, "ceiling_z_m": floor_z + height,
        "floor": {"object_name": "fixture.floor", "world_z_min_m": floor_z, "world_z_max_m": floor_z},
        "ceiling": {"object_name": "fixture.ceiling", "world_z_min_m": floor_z + height,
                    "world_z_max_m": floor_z + height},
    }


def convert_with_synthetic_floor(source, generation_input, config, provider):
    cfg = dict(config)
    if cfg.get("sceneweaver_native_size_semantics") == "released_object_dimensions_rounded_2dp":
        path, selection = _select_layout(source, cfg)
        _, height, _ = canonical_room(generation_input)
        cfg["sceneweaver_native_floor_frame"] = synthetic_floor_frame(
            path, selection["selected_iteration"], height
        )
    return convert_scene_weaver(source, generation_input, cfg, provider)
