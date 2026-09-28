"""Reaggregate a retained complete report using its derived, pinned v8 scorer.

Read-only and no model calls. This is score replay, not regeneration of API
responses. Run in a fresh -I interpreter to prevent mixed benchmark imports.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys


def replay(report, coverage):
    original = report['judgement_coverage_summary']
    if original.get('infrastructure_failure_metrics') or original.get('status') == 'infrastructure_failure':
        raise ValueError('Infrastructure failure is not a complete scored report')
    ids = report['canonical_object_denominator']['ordered_object_ids']
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Invalid canonical object inventory')
    sources = {**report['layer_reports']['l1_physical_plausibility']['metrics'],
               **report['reports']['scene_quality']['metrics']}
    projections = {metric: coverage.metric_projection(metric, sources[metric], ids) for metric in coverage.WEIGHTS}
    for metric, projection in projections.items():
        ledger = projection['observed_scoring']
        if ledger != original['metric_projections'][metric]['observed_scoring']:
            raise ValueError('Metric/object replay differs: ' + metric)
        if ledger is not None:
            for item in [ledger, *(ledger.get('placement_components') or {}).values()]:
                if set(item['effective_object_burdens']) != set(ids) or not isinstance(item['events'], list):
                    raise ValueError('Incomplete object ledger: ' + metric)
    recomputed = coverage.aggregate(projections)
    for key in ('score', 'observed_score', 'judgement_coverage_fraction'):
        left, right = original[key], recomputed[key]
        if left != right and not (isinstance(left, (int, float)) and isinstance(right, (int, float)) and math.isclose(left, right, abs_tol=1e-10)):
            raise ValueError('Scene replay differs: ' + key)
    if recomputed['eligible'] != original['eligible']:
        raise ValueError('Eligibility replay differs')
    return {'status': 'passed', 'objects': len(ids), 'metrics': sorted(projections),
            'score': recomputed['score'], 'coverage': recomputed['judgement_coverage_fraction'],
            'replay_scope': 'metric_object_and_scene_scoring_from_retained_report', 'api_response_regenerated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.release.resolve() / 'src'))
    from benchmark.camera_cal_scene_level.uniform import verify_source
    identity = verify_source(args.release.resolve() / 'release_manifest.json', required=True)
    from benchmark.evaluator import judgement_coverage
    report = json.loads(args.report.read_text())
    result = replay(report, judgement_coverage)
    result.update(report_sha256=hashlib.sha256(args.report.read_bytes()).hexdigest(), evaluator_identity=identity)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
