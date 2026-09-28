"""Independently verify a completed room tree before reusing it on resume."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

COMPAT = Path('/Users/han_mohan/Desktop/Layout_DDD/Support/artifacts/releases/nonrect_materialization_compat_sol_tolerance_v1_20260924')
sys.path.insert(0, str(COMPAT/'src'))
from benchmark.non_rectangular.materialization import verify_completed_nonrect_materialization  # noqa: E402


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--materialized', type=Path, required=True)
    parser.add_argument('--generation-root', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--room', required=True)
    args = parser.parse_args()
    root = args.materialized.resolve()
    checked = verify_completed_nonrect_materialization(root)
    source = json.loads((root/'source_identity.json').read_text())
    expected = (args.generation_root.resolve()/args.model/args.scene)
    if source.get('room_id') != args.room:
        raise ValueError('Room identity differs from previous preparation')
    artifacts = source.get('artifacts')
    if not isinstance(artifacts, dict) or len(artifacts) != 6:
        raise ValueError('Incomplete source identity in previous preparation')
    for record in artifacts.values():
        path = Path(record['path']).resolve()
        if not path.is_relative_to(expected) or sha(path) != record['sha256']:
            raise ValueError('Materialized source input changed')
    print(json.dumps({'status': 'verified', 'materialized': str(root),
        'identity_sha256': checked.identity_sha256, 'room_id': args.room}))


if __name__ == '__main__':
    main()
