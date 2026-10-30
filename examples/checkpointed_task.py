#!/usr/bin/env python3
"""Example of a resumable task; checkpoints belong to the task, not its guard.

Each step produces a deterministic file, then an atomic progress marker. If
interrupted after output but before the marker, replaying that step is safe.
"""
import argparse
import json
import os
from pathlib import Path
import sys


def atomic_write(path, data):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def run(workdir, steps, interrupt_after=None):
    if steps <= 0:
        raise ValueError('steps must be positive')
    workdir.mkdir(parents=True, exist_ok=True)
    checkpoint = workdir / 'progress.json'
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding='utf-8'))
        if state.get('format') != 1 or state.get('steps') != steps or not isinstance(state.get('completed'), int):
            raise ValueError('incompatible checkpoint; inspect it before resuming')
        completed = state['completed']
        if not 0 <= completed <= steps:
            raise ValueError('invalid checkpoint progress')
    else:
        completed = 0
    for step in range(completed, steps):
        output = workdir / f'step-{step:04d}.txt'
        atomic_write(output, f'deterministic result for step {step}\n')
        atomic_write(checkpoint, json.dumps({'format': 1, 'steps': steps, 'completed': step + 1}) + '\n')
        if interrupt_after is not None and step + 1 == interrupt_after:
            print(f'checkpointed={step + 1}; simulated interruption', flush=True)
            return 75
    print(f'completed={steps}', flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description='Resumable task example (not automatic Arena integration)')
    parser.add_argument('--workdir', type=Path, required=True, help='persistent small checkpoint/output directory')
    parser.add_argument('--steps', type=int, default=5)
    parser.add_argument('--interrupt-after', type=int, help='test-only simulated stop after this step')
    args = parser.parse_args()
    try:
        return run(args.workdir, args.steps, args.interrupt_after)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'checkpoint_error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
