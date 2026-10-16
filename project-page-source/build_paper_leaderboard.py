"""Build published Table 1 averages separately from available-case statistics.

Default: python3 project-page-source/build_paper_leaderboard.py
Check:   python3 project-page-source/build_paper_leaderboard.py --check
Optional --verify-text accepts the extracted PDF page 6 for a 64-cell check.

Only paper_leaderboard.json is written. The case-derived leaderboard, example
records, fine metrics and images are neither read nor modified.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MODEL_KEYS = {
    'open-space': ('gpt6_astra_high', 'gpt56sol', 'anthropic_opus5', 'anthropic_fable5',
                   'anthropic_sonnet5', 'kimi_k3', 'glm53', 'hy4_preview'),
    'multi-room': ('gpt6_astra_high', 'gpt-5.6-sol', 'claude-opus-5-aihub', 'claude-fable-5-aihub',
                   'claude-sonnet-5-aihub', 'kimi-k3', 'glm-5.3', 'hy4-preview-tokenhub'),
}
PRINCIPLES = ('plausibility', 'visual_coherence', 'spatial_reasoning')


def scores(model):
    return [model['overall'], *(model['submetric_scores'][key] for key in PRINCIPLES)]


def build(source, pdf):
    if source.get('schema_version') != 1 or source.get('data_kind') != 'paper_table1_model_averages':
        raise ValueError('Unsupported paper-average source schema.')
    provenance = source['source']
    if (provenance.get('score_table'), provenance.get('score_page'), provenance.get('count_page')) != (1, 6, 18):
        raise ValueError('Expected Table 1 on PDF page 6 and populations on page 18.')
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != provenance['sha256']:
        raise ValueError('The public PDF differs from the version pinned by paper-table1.json.')
    if [board['id'] for board in source['leaderboard_views']] != list(MODEL_KEYS):
        raise ValueError('Expected only the WF and RC paper boards, in paper order.')
    boards = []
    for board in source['leaderboard_views']:
        scope = board['id']
        if tuple(model['key'] for model in board['models']) != MODEL_KEYS[scope]:
            raise ValueError(f'{scope}: model identities/order differ from Table 1.')
        models = []
        for model in board['models']:
            if set(model['submetric_scores']) != set(PRINCIPLES):
                raise ValueError('Paper-average output contains only the three principles.')
            for value in scores(model):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0 <= value <= 100
                        or Decimal(str(value)) != Decimal(str(value)).quantize(Decimal('0.1'))):
                    raise ValueError(f'Invalid one-decimal Table 1 value for {model["key"]}: {value}')
            expected = 20 if scope == 'open-space' else 9 if model['key'] == 'hy4-preview-tokenhub' else 10
            if model.get('scored_case_count') != expected:
                raise ValueError(f'Paper population differs for {model["key"]}.')
            models.append({key: deepcopy(model[key]) for key in ('key', 'label', 'overall', 'submetric_scores', 'scored_case_count')})
        boards.append({'id': scope, 'label': board['label'], 'count_unit': board['count_unit'], 'models': models})
    return {'schema_version': 1, 'data_kind': 'paper_table1_model_averages',
            'source': deepcopy(provenance), 'default_leaderboard_view': 'open-space', 'leaderboard_views': boards}


def verify_transcription(output, text):
    """Compare each model/metric cell against the supplied page-6 extraction."""
    sections = {'open-space': {}, 'multi-room': {}}
    scope = None
    for line in text.split('Table2:')[0].splitlines():
        if 'Open space' in line:
            scope = 'open-space'
        if 'With wall layouts' in line:
            scope = 'multi-room'
        match = re.search(r'(Overall|PhysicalV|VisualC|Human-use)\s+(.+)$', line)
        if match and scope:
            values = re.findall(r'\d+\.\d', match[2])
            if len(values) != 8:
                raise ValueError('Expected eight model columns in every Table 1 row.')
            sections[scope][match[1]] = values
    checked = 0
    for board in output['leaderboard_views']:
        for index, model in enumerate(board['models']):
            for metric, value in zip(('Overall', 'PhysicalV', 'VisualC', 'Human-use'), scores(model)):
                expected = sections[board['id']][metric][index]
                if Decimal(str(value)) != Decimal(expected):
                    raise ValueError(f'Transcription mismatch: {board["id"]}/{model["key"]}/{metric}.')
                checked += 1
    if checked != 64:
        raise ValueError(f'Expected 64 verified score cells, got {checked}.')
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('paper-table1.json'))
    parser.add_argument('--site', type=Path, default=ROOT / 'project-page')
    parser.add_argument('--verify-text', type=Path, help='Optional exact PDF page-6 text extraction for all 64 numeric cells.')
    parser.add_argument('--check', action='store_true', help='Verify the generated public file without writing.')
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding='utf-8'))
    output = build(source, args.site / 'paper/GREP.pdf')
    checked = verify_transcription(output, args.verify_text.read_text(encoding='utf-8')) if args.verify_text else None
    target = args.site / 'paper_leaderboard.json'
    payload = (json.dumps(output, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    changed = not target.is_file() or target.read_bytes() != payload
    if args.check and changed:
        raise SystemExit('paper_leaderboard.json needs rebuilding.')
    if changed and not args.check:
        with tempfile.NamedTemporaryFile(prefix='.paper-averages-', dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(payload)
            except BaseException:
                handle.close(); temporary.unlink(missing_ok=True)
                raise
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps({'changed': changed, 'model_rows': 16, 'score_cells': 64,
                      'verified_against_page6_text': checked, 'pdf_sha256': source['source']['sha256']}))


if __name__ == '__main__':
    main()
