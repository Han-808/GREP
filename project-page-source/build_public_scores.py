"""Project RC rankings from layouts whose every required room has a score.

Preserve all layout/room values and WF data. A layout receives scoring_eligible
only when its positive room_count matches its room list and every room score is
a finite number. Eligible layout scores already contain room-area weighting;
model Overall, principles and fine metrics are their equal-weight means.

Public counts distinguish generated layouts/rooms, scoring-eligible layouts,
individually scored rooms, and rooms inside ranked layouts. completed/expected
retain their existing generation meanings. The captured data/website source is
never read or written by this builder.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
PRINCIPLES = {
    'plausibility': 'physical',
    'spatial_reasoning': 'functional_semantics',
    'visual_coherence': 'visual_coherence',
}
FINE_METRICS = (
    'collision', 'support', 'oob', 'scale_consistency', 'style_consistency',
    'object_pairing_consistency', 'functional_consistency', 'semantic_placement_consistency',
)


def finite_score(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def eligible_layout(layout):
    rooms = layout.get('rooms')
    count = layout.get('room_count')
    return (isinstance(rooms, list) and bool(rooms)
            and isinstance(count, int) and not isinstance(count, bool)
            and count == len(rooms)
            and all(isinstance(room, dict) and finite_score(room.get('score')) for room in rooms))


def mean(values, label):
    if not values:
        return None
    if not all(finite_score(value) for value in values):
        raise ValueError(f'Eligible layout is missing a finite {label} value; refusing to impute.')
    return math.fsum(values) / len(values)


def project(leaderboard, multi_room):
    """Return detached public projections; never mutate caller-owned records."""
    ranking, scenes = deepcopy(leaderboard), deepcopy(multi_room)
    boards = [board for board in ranking['leaderboard_views'] if board['id'] == 'multi-room']
    if len(boards) != 1:
        raise ValueError('Expected exactly one public multi-room board.')
    board = boards[0]
    models = {model['key']: model for model in board['models']}
    datasets = {dataset['key']: dataset for dataset in scenes['datasets']}
    if len(models) != len(board['models']) or len(datasets) != len(scenes['datasets']):
        raise ValueError('Duplicate RC model keys.')
    if models.keys() != datasets.keys():
        raise ValueError('RC ranking models and example datasets must match exactly.')

    layouts_total = rooms_total = scored_rooms = ranked_layouts = ranked_rooms = 0
    for key, dataset in datasets.items():
        layouts = dataset['layouts']
        ids = [layout['layout_id'] for layout in layouts]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate layout IDs for {key}.')
        for layout in layouts:
            layout['scoring_eligible'] = eligible_layout(layout)
        included = [layout for layout in layouts if layout['scoring_eligible']]
        excluded = [layout for layout in layouts if not layout['scoring_eligible']]
        model = models[key]
        model['overall'] = mean([layout.get('score') for layout in included], 'Overall')
        for principle, field in PRINCIPLES.items():
            model.setdefault('submetric_scores', {})[principle] = mean([layout.get(field) for layout in included], principle)
        for metric in FINE_METRICS:
            model.setdefault('fine_metric_scores', {})[metric] = mean([layout.get('metrics', {}).get(metric) for layout in included], metric)
        model['scored_case_count'] = len(included)
        model['included_layout_ids'] = [layout['layout_id'] for layout in included]
        model['excluded_layout_ids'] = [layout['layout_id'] for layout in excluded]
        layouts_total += len(layouts)
        rooms_total += sum(len(layout.get('rooms', [])) for layout in layouts)
        scored_rooms += sum(finite_score(room.get('score')) for layout in layouts for room in layout.get('rooms', []))
        ranked_layouts += len(included)
        ranked_rooms += sum(len(layout['rooms']) for layout in included)

    counts = scenes.setdefault('counts', {})
    counts.update(models=len(datasets), layouts=layouts_total, scenes=layouts_total,
                  rooms=rooms_total, scored_layouts=ranked_layouts, scored_rooms=scored_rooms)
    scenes.setdefault('reporting_counts', {}).update(scored_rooms=scored_rooms,
                                                  ranked_layouts=ranked_layouts,
                                                  ranked_rooms=ranked_rooms)
    board['coverage_note'] = f'{len(models)} models · {ranked_layouts} scored layouts'
    return ranking, scenes


def serialize(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def write_if_changed(path, payload):
    if path.read_bytes() == payload:
        return False
    with tempfile.NamedTemporaryFile(prefix='.grep-scores-', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
        except BaseException:
            handle.close(); temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=ROOT / 'project-page')
    parser.add_argument('--check', action='store_true', help='Verify that public files already match the projection, without writing.')
    args = parser.parse_args()
    ranking_path = args.site / 'leaderboard.json'
    scenes_path = args.site / 'multi_room_sceneboard.json'
    ranking = json.loads(ranking_path.read_text(encoding='utf-8'))
    scenes = json.loads(scenes_path.read_text(encoding='utf-8'))
    projected = project(ranking, scenes)
    # Validate and serialize both outputs before either file is written.
    payloads = [serialize(data) for data in projected]
    paths = [ranking_path, scenes_path]
    stale = [path.name for path, payload in zip(paths, payloads) if path.read_bytes() != payload]
    if args.check and stale:
        raise SystemExit('Public score projection needs rebuilding: ' + ', '.join(stale))
    if not args.check:
        for path, payload in zip(paths, payloads):
            write_if_changed(path, payload)
    print(json.dumps({'changed': [] if args.check else stale,
                      'ranked_layouts': projected[1]['reporting_counts']['ranked_layouts'],
                      'ranked_rooms': projected[1]['reporting_counts']['ranked_rooms'],
                      'individually_scored_rooms': projected[1]['reporting_counts']['scored_rooms']}))


if __name__ == '__main__':
    main()
