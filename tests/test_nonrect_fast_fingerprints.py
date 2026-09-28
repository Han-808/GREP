"""Exercise production function bodies with fingerprint functions that fail.

Pure Python probes deliberately stop at known structural checks. Blender probes
in validate_blender.py separately execute the complete worker on real rooms.
"""
import ast
import json
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.requires_local_data

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from nonrect_fast_v1 import content_policy, derive


def source_file(group, rel):
    pins = derive.read(derive.PINS)
    root = pins['materializer_root' if group == 'materializer' else 'evaluator_root']
    path = Path(root) / rel
    if not path.is_file():
        pytest.skip('Pinned local source is unavailable')
    return path.read_text()


def function(source, name, namespace):
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<production-function>', 'exec'), namespace)
    return namespace[name]


class Object(dict):
    __hash__ = object.__hash__
    name = 'root'
    type = 'MESH'
    parent = None
    hide_render = False
    hide_viewport = False
    def hide_get(self):
        return False


def expensive(*args, **kwargs):
    raise AssertionError('Expensive content fingerprint was invoked')


def test_materializer_fast_path_never_calls_any_fingerprint(monkeypatch):
    original = source_file('materializer', 'src/benchmark/materialization/blend_inspector_worker.py')
    modified = derive.inspector(original)
    namespace = {name: name for name in ['INSTANCE_ID_PROPERTY', 'EVALUATOR_ID_PROPERTY', 'CANONICAL_ID_PROPERTY', 'ASSET_ID_PROPERTY', 'ROLE_PROPERTY']}
    namespace.update({
        '_descendants': lambda root: [], '_check': lambda checks, name, okay, details, code: checks.append({'name': name, 'passed': okay}),
        '_validate_rigid_uniform_matrix': lambda matrix: {'valid': True, 'uniform_scale': 1},
        '_observed_bounds': lambda *a: {'local_bbox_size_m_observed': [1, 1, 1]},
        '_compare_expected_geometry': lambda *a, **kw: [], '_vec3_or_none': lambda value: None,
        '_first_text': lambda *a: next((v for v in a if v), None), '_matrix_rows': lambda m: [],
        '_technically_hidden': lambda obj: False, '_custom_properties': lambda root: {},
        '_json_scalar_or_vector': lambda v: v, '_close_json': lambda a, b: a == b,
        '_geometry_fingerprint': expensive, '_material_fingerprint': expensive, '_asset_assembly_fingerprint': expensive,
        'skipped': content_policy.skipped, 'receipt': content_policy.receipt, 'json': json,
    })
    root = Object(ROLE_PROPERTY='instance_root', INSTANCE_ID_PROPERTY='id')
    root.matrix_world = type('Matrix', (), {'translation': [0, 0, 0]})()
    fn = function(modified, '_inspect_instance', namespace)
    monkeypatch.setenv(content_policy.ENV, 'off')
    record, descendants, checks = fn(root, instance_id='id', expected={}, mode='sanitized')
    assert not any(key in record for key in content_policy.HASH_FIELDS)
    assert record['content_fingerprint_validation']['status'] == 'skipped_by_policy'
    assert record['local_bbox_size_m'] == [1, 1, 1]
    assert all(check['passed'] for check in checks)
    # Object identity mismatch remains a real failed structural check.
    _, _, checks = fn(root, instance_id='wrong', expected={}, mode='sanitized')
    assert not all(check['passed'] for check in checks)
    monkeypatch.delenv(content_policy.ENV)
    with pytest.raises(AssertionError, match='Expensive'):
        fn(root, instance_id='id', expected={}, mode='sanitized')


def test_evidence_fast_path_skips_hashes_but_still_rejects_structure(monkeypatch):
    modified = derive.prepared_worker(source_file('evaluator', 'src/benchmark/rendering/blender_prepared_worker.py'))
    namespace = {'_descendants': lambda root: [], '_object_render_state_mismatches': lambda objects: [],
                 '_geometry_fingerprint': expensive, '_material_fingerprint': expensive, '_asset_assembly_fingerprint': expensive,
                 '_id_property_value': lambda value: value, '_close_json': lambda a, b: a == b,
                 'INSTANCE_ID_PROPERTY': 'instance', 'skipped': content_policy.skipped,
                 'validate_record': content_policy.validate_record, 'json': json}
    fn = function(modified, '_validate_instance_geometry', namespace)
    root = Object(instance='wrong')
    expected = {'metadata': {'materialization': {'instance_id': 'correct',
                'content_fingerprint_validation': content_policy.receipt()}}}
    monkeypatch.setenv(content_policy.ENV, 'off')
    with pytest.raises(RuntimeError, match='instance_id property mismatch'):
        fn(evaluator_id='x', root=root, meshes=[root], expected=expected)
    root.type = 'LIGHT'
    with pytest.raises(RuntimeError, match='disallowed object types'):
        fn(evaluator_id='x', root=root, meshes=[root], expected=expected)
    root.type = 'MESH'
    monkeypatch.delenv(content_policy.ENV)
    with pytest.raises(ValueError, match='Strict validation'):
        fn(evaluator_id='x', root=root, meshes=[root], expected=expected)
    expected['metadata']['materialization'].pop('content_fingerprint_validation')
    with pytest.raises(AssertionError, match='Expensive'):
        fn(evaluator_id='x', root=root, meshes=[root], expected=expected)


def test_render_and_export_functions_not_edited_and_strict_fingerprint_body_preserved():
    before = source_file('evaluator', 'src/benchmark/rendering/blender_prepared_worker.py')
    after = derive.prepared_worker(before)
    original = ast.parse(before)
    modified = ast.parse(after)
    funcs_before = {n.name: n for n in original.body if isinstance(n, ast.FunctionDef)}
    funcs_after = {n.name: n for n in modified.body if isinstance(n, ast.FunctionDef)}
    for name in funcs_before.keys() - {'_validate_instance_geometry'}:
        assert ast.dump(funcs_before[name]) == ast.dump(funcs_after[name]), name
    prior = funcs_before['_validate_instance_geometry'].body
    guarded = next(n for n in funcs_after['_validate_instance_geometry'].body
                   if isinstance(n, ast.If) and ast.unparse(n.test) == 'not skipped()')
    start = next(i for i, n in enumerate(prior) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'expected_geometry_sha256')
    end = next(i for i, n in enumerate(prior) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'comparisons')
    assert ast.dump(ast.Module(body=guarded.body, type_ignores=[])) == ast.dump(ast.Module(body=prior[start:end], type_ignores=[]))
    # All transform, bbox, center and scale checks after the guarded block remain.
    after_body = funcs_after['_validate_instance_geometry'].body
    offset = after_body.index(guarded) + 1
    assert ast.dump(ast.Module(body=after_body[offset:], type_ignores=[])) == ast.dump(ast.Module(body=prior[end:], type_ignores=[]))


def test_evaluator_loader_checks_every_record_before_scoring():
    before = source_file('evaluator', 'src/benchmark/camera_cal_scene_level/uniform.py')
    after = derive.uniform(before)
    assert "for item in scene.get('objects', []):\n            validate_record" in after
    assert after.index('validate_record((item') < after.index('load_collision_geometry_manifest(paths')
    # Everything else in the loader/scorer is byte-identical.
    inserted = "\n        from benchmark.materialization.nonrect_content_policy import validate_record\n        for item in scene.get('objects', []):\n            validate_record((item.get('metadata') or {}).get('materialization') or {})"
    assert after.replace(inserted, '') == before
