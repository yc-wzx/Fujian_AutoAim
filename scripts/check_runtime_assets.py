#!/usr/bin/env python3
"""Verify current external runtime dependencies without modifying files."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('model', 'sdk'), help='Check only the selected resource kind')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'docs/artifacts.json').read_text(encoding='utf-8'))
    items = [item for item in manifest['files'] if args.kind is None or item['kind'] == args.kind]
    failures = []
    for item in items:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Resource path is outside this repository')
        if not path.is_file():
            failures.append(f"Missing: {item['path']}; see docs/dependencies.md")
            continue
        if path.stat().st_size != item['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            failures.append(f"Content differs: {item['path']}")
    if failures:
        print('\n'.join(failures), file=sys.stderr)
        return 1
    print(f"Verified {len(items)} current runtime resources; no files changed or application started.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
