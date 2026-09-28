"""Build a separately identified runtime from pinned, read-only source files.

Only the sealed evaluator's file inventory, pinned materializer code/resources,
and the explicitly vendored runner scripts are copied. No experiment outputs,
assets, credentials, or working-tree changes are copied. Each edit uses a unique
anchor; an upstream drift fails closed. diffs.json/diffs.patch describe all edits.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
PINS = HERE / "source_pins.json"
VERSION = "nonrect_v8_trusted_content_off_queue_v1"


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Patch anchor is not unique: " + before[:90])
    return text.replace(before, after, 1)


def guard_region(text, start, end, condition, otherwise=""):
    a, b = text.index(start), text.index(end, text.index(start))
    block = text[a:b]
    guarded = "    if " + condition + ":\n" + "".join("    " + line if line.strip() else line for line in block.splitlines(True))
    return text[:a] + guarded + otherwise + text[b:]


def inspector(text):
    text = replace_once(text, "import bpy\n", "import bpy\nfrom nonrect_content_policy import skipped, receipt\n")
    text = guard_region(text, "    geometry_fingerprint = _geometry_fingerprint(root, meshes)",
                        '    if mode in {"registered_native", "public_native"}:',
                        "not skipped()", "    elif mode != 'sanitized':\n        raise ValueError('Content-off policy is restricted to trusted sanitized nonrect inputs')\n")
    # No fabricated or null digest fields. Structural fields are unchanged.
    first = '        "geometry_sha256": geometry_fingerprint,\n        "mesh_data_sha256": mesh_data_fingerprint,'
    text = replace_once(text, first, '        **({"content_fingerprint_validation": receipt()} if skipped() else {\n' + first)
    text = replace_once(text, '        "asset_assembly_sha256": asset_assembly_fingerprint,\n        "technical_visibility":',
                        '        "asset_assembly_sha256": asset_assembly_fingerprint,\n        }),\n        "technical_visibility":')
    return replace_once(text, '        "status": "passed" if passed else "failed",',
                        '        "status": ("passed_structural_checks" if skipped() else "passed") if passed else "failed",\n        **({"content_fingerprint_validation": receipt()} if skipped() else {}),')


def prepared_worker(text):
    text = replace_once(text, "INSTANCE_ID_PROPERTY =", "from nonrect_content_policy import skipped, validate_record\n\nINSTANCE_ID_PROPERTY =")
    text = replace_once(text, '    expected_geometry_sha256 = str(', '    validate_record(materialization)\n    expected_geometry_sha256 = str(')
    return guard_region(text, "    expected_geometry_sha256 = str(", "    comparisons = {", "not skipped()")


def materialization(text):
    text = replace_once(text, "from __future__ import annotations\n", "from __future__ import annotations\n\nfrom benchmark.materialization.nonrect_content_policy import inspection_accepted\n")
    text = replace_once(text, 'if not isinstance(inspection, Mapping) or inspection.get("status") != "passed":',
                        'if not inspection_accepted(inspection):')
    return replace_once(text, 'if _read_json(paths["inspection_report"]).get("status") != "passed":',
                        'if not inspection_accepted(_read_json(paths["inspection_report"])):')


def backend(text):
    text = replace_once(text, "from __future__ import annotations\n", "from __future__ import annotations\n\nfrom benchmark.materialization.nonrect_content_policy import inspection_accepted, skipped\n")
    text = replace_once(text, 'if report.get("status") != "passed":', 'if not inspection_accepted(report):')
    return replace_once(text, '"status": "built_and_independently_inspected",',
                        '"status": "built_and_structurally_inspected" if skipped() else "built_and_independently_inspected",')


def uniform(text):
    # Validate the mode at the evaluator loading boundary, before any model call.
    return replace_once(text, "        scene = read(paths['scene'])", "        scene = read(paths['scene'])\n        from benchmark.materialization.nonrect_content_policy import validate_record\n        for item in scene.get('objects', []):\n            validate_record((item.get('metadata') or {}).get('materialization') or {})")


TRANSFORMS = {
    "materializer/src/benchmark/materialization/blend_inspector_worker.py": inspector,
    "materializer/src/benchmark/non_rectangular/materialization.py": materialization,
    "materializer/src/benchmark/non_rectangular/blender_materialization.py": backend,
    "evaluator/src/benchmark/rendering/blender_prepared_worker.py": prepared_worker,
    "evaluator/src/benchmark/camera_cal_scene_level/uniform.py": uniform,
}


def tree_digest(files):
    return hashlib.sha256("".join(rel + "\0" + digest + "\n" for rel, digest in sorted(files.items())).encode()).hexdigest()


def checked_copy(source, destination, expected):
    if sha(source) != expected:
        raise ValueError("Pinned source changed: " + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if sha(destination) != expected:
        raise ValueError("Source changed while copying: " + str(source))


def build(destination):
    destination = Path(destination).resolve()
    pins = read(PINS)
    protected = Path('/Users/han_mohan/Desktop/Layout_DDD').resolve()
    if destination.is_relative_to(protected) or protected.is_relative_to(destination):
        raise ValueError("Derived runtime must be outside the active main repository")
    destination.mkdir(parents=True, exist_ok=False)
    evaluator = Path(pins["evaluator_root"])
    if sha(evaluator / "release_manifest.json") != pins["evaluator_manifest_sha256"]:
        raise ValueError("Sealed v8 manifest changed")
    release = read(evaluator / "release_manifest.json")
    originals = {}
    for group, source, files in (
        ("evaluator", evaluator, release["files"]),
        ("materializer", Path(pins["materializer_root"]), pins["materializer_files"]),
    ):
        for rel, expected in files.items():
            if Path(rel).is_absolute() or ".." in Path(rel).parts:
                raise ValueError("Unsafe source path")
            checked_copy(source / rel, destination / group / rel, expected)
            originals[group + "/" + rel] = expected
    for rel, record in pins["upstream"].items():
        checked_copy(HERE / "upstream" / rel, destination / rel, record["sha256"])
        originals[rel] = record["sha256"]
    edits, patches = {}, []

    def edit(rel, transform):
        path = destination / rel
        before = path.read_text()
        after = transform(before)
        compile(after, str(path), "exec")
        path.write_text(after)
        edits[rel] = {"before_sha256": originals[rel], "after_sha256": sha(path)}
        patches.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="source/" + rel, tofile="derived/" + rel))

    for rel, transform in TRANSFORMS.items():
        edit(rel, transform)
    for group in ("materializer", "evaluator"):
        path = destination / group / "src/benchmark/materialization/nonrect_content_policy.py"
        shutil.copyfile(HERE / "content_policy.py", path)
    catalog = "scripts/nonrect_merged30_v8_api2_sol_catalog/"

    def rebase(text, symbol, source, target):
        # Constants only; preserve every camera/render/scoring choice.
        import re
        pattern = rf"(?m)^{symbol} = Path\((?:[^\n]*\n)*?\)" if f"{symbol} = Path(\n" in text else rf"(?m)^{symbol} = Path\([^\n]+\)"
        text, count = re.subn(pattern, f"{symbol} = Path({str(target)!r})", text, count=1)
        if count != 1:
            raise ValueError("Missing runtime path constant " + symbol)
        return text

    edit(catalog + "materialize_room.py", lambda t: rebase(t, "COMPAT", None, destination / "materializer"))
    edit(catalog + "verify_materialized.py", lambda t: rebase(t, "COMPAT", None, destination / "materializer"))

    def case_builder(t):
        t = rebase(t, "RELEASE", None, destination / "evaluator")
        t = replace_once(t, '                "geometry_sha256",', '                "content_fingerprint_validation",\n                "geometry_sha256",')
        t = replace_once(t, '    inspection = read_json(room_dir / "inspection_report.json")',
            '    inspection = read_json(room_dir / "inspection_report.json")\n    from benchmark.materialization.nonrect_content_policy import inspection_accepted, validate_record, skipped, receipt\n    if not inspection_accepted(inspection):\n        raise ValueError("Materializer/evidence policy mismatch or failed structural inspection")')
        t = replace_once(t, '        metadata = item.get("metadata")', '        validate_record(record)\n        metadata = item.get("metadata")')
        t = replace_once(t, '        "status": "ready",', '        "status": "ready",\n        "content_fingerprint_validation": receipt() if skipped() else {"mode": "strict"},')
        t = t.replace('"sealed_v8_BlenderRenderer', '"derived_v8_BlenderRenderer')
        return t

    edit(catalog + "build_case.py", case_builder)
    def evidence(t):
        t = replace_once(t, '    from blender_worker import (', '    from nonrect_content_policy import skipped, receipt\n\n    from blender_worker import (')
        return replace_once(t, '        "backend": "nonrect_prepared_scene_read_only_oneoff_v1",',
                            '        "backend": "nonrect_prepared_scene_read_only_oneoff_v1",\n        **({"content_fingerprint_validation": receipt()} if skipped() else {}),')
    edit(catalog + "nonrect_evidence_worker.py", evidence)
    # A NEW manifest with a new source root and new hashes. Old manifest is untouched.
    files = {rel: sha(destination / "evaluator" / rel) for rel in release["files"]}
    policy_rel = "src/benchmark/materialization/nonrect_content_policy.py"
    files[policy_rel] = sha(destination / "evaluator" / policy_rel)
    derived = {**release, "source_root": str(destination / "evaluator"), "files": files,
               "source_tree_sha256": tree_digest(files), "derivation_id": VERSION,
               "parent_release_manifest_sha256": pins["evaluator_manifest_sha256"],
               "content_fingerprint_validation_default": "strict", "fast_policy": "explicit_off_trusted_input_only"}
    (destination / "evaluator/release_manifest.json").write_text(json.dumps(derived, indent=2) + "\n")
    (destination / "diffs.patch").write_text("".join(patches))
    derived_files = {str(p.relative_to(destination)): sha(p) for p in destination.rglob('*') if p.is_file()}
    manifest = {"schema_version": VERSION, "parent_evaluator_manifest_sha256": pins["evaluator_manifest_sha256"],
                "source_pins_sha256": sha(PINS), "original_files": originals, "edits": edits,
                "files": derived_files, "source_tree_sha256": tree_digest(derived_files)}
    (destination / "derivation_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(destination):
    destination = Path(destination).resolve()
    manifest = read(destination / "derivation_manifest.json")
    if manifest['schema_version'] != VERSION or manifest['source_pins_sha256'] != sha(PINS):
        raise ValueError("Unknown derived runtime identity")
    if tree_digest(manifest['files']) != manifest['source_tree_sha256']:
        raise ValueError("Invalid derived runtime digest")
    for rel, expected in manifest['files'].items():
        path = destination / rel
        if not path.resolve().is_relative_to(destination) or path.is_symlink() or sha(path) != expected:
            raise ValueError("Derived runtime changed: " + rel)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify(args.destination) if args.verify else build(args.destination)
    print(json.dumps({"derivation_id": VERSION, "runtime": str(args.destination.resolve()),
                      "source_tree_sha256": result['source_tree_sha256']}))
