"""Publication-boundary checks for RC inclusion, preserved records and Table 1."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import build_public_scores as builder

SITE = builder.ROOT / 'project-page'


class PublicScoresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ranking = json.loads((SITE / 'leaderboard.json').read_text(encoding='utf-8'))
        cls.scenes = json.loads((SITE / 'multi_room_sceneboard.json').read_text(encoding='utf-8'))
        cls.projected_ranking, cls.projected_scenes = builder.project(cls.ranking, cls.scenes)

    def test_all_original_layout_room_and_fine_values_are_preserved(self):
        before, after = deepcopy(self.scenes['datasets']), deepcopy(self.projected_scenes['datasets'])
        for datasets in (before, after):
            for dataset in datasets:
                for layout in dataset['layouts']:
                    layout.pop('scoring_eligible', None)
        self.assertEqual(before, after)
        self.assertEqual(sum(len(d['layouts']) for d in after), 70)
        self.assertEqual(sum(len(l['rooms']) for d in after for l in d['layouts']), 217)

    def test_wf_and_all_other_board_data_are_untouched(self):
        before, after = deepcopy(self.ranking), deepcopy(self.projected_ranking)
        before['leaderboard_views'] = [b for b in before['leaderboard_views'] if b['id'] != 'multi-room']
        after['leaderboard_views'] = [b for b in after['leaderboard_views'] if b['id'] != 'multi-room']
        self.assertEqual(before, after)
        status = json.loads((SITE / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(len(d['scenes']) for d in status['datasets']), 150)
        self.assertEqual([b['id'] for b in self.projected_ranking['leaderboard_views']], ['open-space', 'multi-room'])
        self.assertEqual([m['key'] for b in self.ranking['leaderboard_views'] for m in b['models']],
                         [m['key'] for b in self.projected_ranking['leaderboard_views'] for m in b['models']])

    def test_count_semantics_and_hy4_exclusion(self):
        counts, reporting = self.projected_scenes['counts'], self.projected_scenes['reporting_counts']
        self.assertEqual({k: counts[k] for k in ['models', 'layouts', 'rooms', 'scored_layouts', 'scored_rooms']},
                         {'models': 7, 'layouts': 70, 'rooms': 217, 'scored_layouts': 69, 'scored_rooms': 216})
        self.assertEqual((reporting['ranked_layouts'], reporting['ranked_rooms']), (69, 213))
        board = next(b for b in self.projected_ranking['leaderboard_views'] if b['id'] == 'multi-room')
        self.assertEqual(board['coverage_note'], '7 models · 69 scored layouts')
        for model in board['models']:
            old = next(m for b in self.ranking['leaderboard_views'] if b['id'] == 'multi-room' for m in b['models'] if m['key'] == model['key'])
            self.assertEqual((model['completed'], model['expected']), (old['completed'], old['expected']))
            self.assertEqual(model['scored_case_count'], 9 if model['key'] == 'hy4-preview-tokenhub' else 10)
            self.assertEqual(model['excluded_layout_ids'], ['layout_09'] if model['key'] == 'hy4-preview-tokenhub' else [])

    def test_all_13_previously_matching_rows_and_hy4_match_table1(self):
        # Published Table 1 values, ordered Overall / Physical / Visual / Human-Use.
        expected = {
            'open-space': {'gpt56sol': ['73.7','81.0','84.8','66.4'], 'anthropic_opus5': ['81.5','90.3','95.3','72.4'],
                           'anthropic_fable5': ['77.9','77.8','95.1','74.8'], 'anthropic_sonnet5': ['65.3','72.4','87.0','56.0'],
                           'kimi_k3': ['73.2','70.2','91.2','72.0'], 'glm53': ['70.8','67.2','90.2','69.7'], 'hy4_preview': ['61.8','55.9','86.8','61.4']},
            'multi-room': {'gpt-5.6-sol': ['73.4','73.8','89.9','69.9'], 'claude-opus-5-aihub': ['69.5','79.4','94.0','57.6'],
                           'claude-fable-5-aihub': ['72.5','79.2','91.5','64.0'], 'claude-sonnet-5-aihub': ['65.7','73.5','96.7','54.0'],
                           'kimi-k3': ['66.6','75.4','92.9','55.1'], 'glm-5.3': ['68.7','72.0','89.5','62.4'],
                           'hy4-preview-tokenhub': ['54.2','54.0','87.5','47.9']},
        }
        values, targets = [], []
        for board in self.projected_ranking['leaderboard_views']:
            for model in board['models']:
                if model['key'] not in expected[board['id']]:
                    continue  # Astra's completed experiments await the original export.
                scores = model['submetric_scores']
                values.extend([model['overall'], scores['plausibility'], scores['visual_coherence'], scores['spatial_reasoning']])
                targets.extend(expected[board['id']][model['key']])
        node = shutil.which('node')
        if node is None:
            bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
            node = str(bundled) if bundled.is_file() else None
        self.assertIsNotNone(node, 'Node is needed to verify the exact browser toFixed behavior.')
        result = subprocess.run([node, '-e', "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(s).map(v=>Number(v).toFixed(1)))));"],
                                input=json.dumps(values), capture_output=True, text=True, check=True)
        self.assertEqual(len(targets), 56)
        self.assertEqual(json.loads(result.stdout), targets)

    def test_all_eight_hy4_fine_metrics_use_only_included_layouts(self):
        dataset = next(d for d in self.scenes['datasets'] if d['key'] == 'hy4-preview-tokenhub')
        selected = [l for l in dataset['layouts'] if l['layout_id'] != 'layout_09']
        model = next(m for b in self.projected_ranking['leaderboard_views'] if b['id'] == 'multi-room' for m in b['models'] if m['key'] == dataset['key'])
        for metric in builder.FINE_METRICS:
            self.assertAlmostEqual(model['fine_metric_scores'][metric], sum(l['metrics'][metric] for l in selected) / 9, places=12)

    def test_eligibility_rejects_missing_malformed_and_nonfinite_rooms(self):
        self.assertTrue(builder.eligible_layout({'room_count': 2, 'rooms': [{'score': 0}, {'score': 100}]}))
        for score in [None, float('nan'), float('inf'), -float('inf'), True, '80']:
            self.assertFalse(builder.eligible_layout({'room_count': 1, 'rooms': [{'score': score}]}))
        self.assertFalse(builder.eligible_layout({'room_count': 2, 'rooms': [{'score': 90}]}))
        self.assertFalse(builder.eligible_layout({'room_count': 0, 'rooms': []}))

    def test_missing_principle_is_not_silently_imputed(self):
        source = deepcopy(self.scenes)
        source['datasets'][0]['layouts'][0]['physical'] = None
        with self.assertRaises(ValueError):
            builder.project(self.ranking, source)

    def test_projection_and_command_are_byte_idempotent_and_read_only_outside_outputs(self):
        reranking, rescene = builder.project(self.projected_ranking, self.projected_scenes)
        self.assertEqual(builder.serialize(reranking), builder.serialize(self.projected_ranking))
        self.assertEqual(builder.serialize(rescene), builder.serialize(self.projected_scenes))
        guarded = [SITE / 'status.json'] + [builder.ROOT / 'data/website/site' / name for name in ['status.json', 'leaderboard.json', 'multi_room_sceneboard.json']]
        hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in guarded}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for name in ['leaderboard.json', 'multi_room_sceneboard.json']:
                shutil.copy2(SITE / name, destination / name)
            command = [__import__('sys').executable, str(Path(builder.__file__)), '--site', directory]
            subprocess.run(command, capture_output=True, check=True)
            first = {p.name: p.read_bytes() for p in destination.iterdir()}
            second = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(second.stdout)['changed'], [])
            self.assertEqual(first, {p.name: p.read_bytes() for p in destination.iterdir()})
            subprocess.run(command + ['--check'], capture_output=True, check=True)
        self.assertEqual(hashes, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in guarded})


if __name__ == '__main__':
    unittest.main()
