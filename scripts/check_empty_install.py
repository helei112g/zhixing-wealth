#!/usr/bin/env python3
"""Read-only first-install check. Never initializes, resets or deletes a ledger."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.common import DomainError
from backend.deployment_guard import require_empty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--environment', choices=('production', 'dev', 'test'), required=True)
    args = parser.parse_args()
    marker = args.data_dir/'.environment.json'
    if marker.is_symlink() or json.loads(marker.read_text()).get('environment') != args.environment:
        raise DomainError('账本环境与目标不一致')
    print(json.dumps(require_empty(args.data_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except (DomainError, OSError, ValueError) as exc: raise SystemExit(str(exc)) from None
