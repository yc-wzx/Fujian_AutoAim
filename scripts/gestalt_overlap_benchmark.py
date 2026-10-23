#!/usr/bin/env python3
"""Frozen-plan local simulator comparison. Default: offline report only.

--execute explicitly runs bounded trials through run_gestalt_live.ps1, never
physical standard/camera/serial backends. Each round has a native postflight.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time

from gestalt_trial_report import summarize, read_status
from gestalt_series_report import receipt_timing
from gestalt_speed_report import audit_native

ROOT = Path(__file__).resolve().parents[1]


def hashes(plan):
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in plan['sha256']}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def report(plan_path):
    plan = read_status(plan_path)
    rounds, pending, issues = [], [], []
    for name in plan['trials']:
        path = plan_path.parent / ('trial-' + name + '.json')
        if not path.exists():
            pending.append(name); continue
        raw = read_status(path)
        if 'shot_trial' not in raw:
            issues.append(name + ': no shot telemetry'); continue
        row = summarize(path)
        row.update(trial=name, profile=plan['profiles'][name],
                   receipt_timing=receipt_timing(raw['shot_trial']['shots']))
        if raw.get('error') or not row['score_checks_passed']:
            issues.append(name + ': controller/scoring error')
        if row['state'] != 'complete' or row['actual_shots'] != plan['shots_per_round']:
            pending.append(name)
        else:
            try:
                row['native_audit'] = audit_native(path, row)
                if not row['native_audit']['passed']: issues.append(name + ': native audit disagrees')
            except (OSError, ValueError, KeyError) as error:
                issues.append(name + ': ' + str(error))
        keys = ['settle_ms', 'prearm', 'hit_goal', 'bench_pose', 'prepare_during_settle']
        if 'fixed_center_prediction' in plan['profiles'][name]: keys.append('fixed_center_prediction')
        for key in keys:
            if row['parameters'].get(key) != plan['profiles'][name][key]:
                issues.append(name + ': profile changed ' + key)
        for key, value in plan['settings'].items():
            if row['parameters'].get(key) != value: issues.append(name + ': setting changed ' + key)
        events = raw['shot_trial']['shots']
        row['overlapped_preparations'] = sum(e.get('prepared_during_previous_settle', False) for e in events)
        gaps = [b['request_qpc_s'] - a['settled_qpc_s'] for a, b in zip(events, events[1:])]
        row['median_settled_to_next_request_s'] = statistics.median(gaps) if gaps else None
        rounds.append(row)
    groups = {}
    for mode in ('control', 'candidate'):
        rows = [r for r in rounds if r['profile']['mode'] == mode and r['state'] == 'complete']
        shots, hits = sum(r['actual_shots'] for r in rows), sum(r['registered_hits'] for r in rows)
        times = [r['receipt_timing']['first_request_to_31st_effective_hit_ack_s'] for r in rows]
        groups[mode] = {'rounds': len(rows), 'shots': shots, 'hits': hits,
                        'hit_rate': hits / shots if shots else None,
                        '31_hit_receipt_times_s': times,
                        'median_31_hit_receipt_s': statistics.median(times) if times and None not in times else None}
    old, new = (groups[k]['median_31_hit_receipt_s'] for k in ('control', 'candidate'))
    ratio = new / old if old and new else None
    complete = not pending and not issues and len(rounds) == len(plan['trials'])
    comparable = all(groups[k]['rounds'] == 2 and groups[k]['shots'] == 100 for k in groups)
    adopt = bool(complete and comparable and ratio is not None and
                 ratio <= plan['criteria']['max_time_ratio'] and
                 groups['candidate']['hit_rate'] >= plan['criteria'].get('min_hit_rate', 0) and
                 groups['candidate']['hit_rate'] >= groups['control']['hit_rate'] - plan['criteria']['max_hit_rate_drop'])
    return {'complete_and_audited': complete, 'pending': pending, 'issues': issues,
            'groups': groups, 'rounds': rounds, 'candidate_to_control_time_ratio': ratio,
            'candidate_adoptable_in_simulator': adopt, 'criteria': plan['criteria'],
            'current_files_match_plan': hashes(plan) == plan['sha256'],
            'scope': 'Small simulator study. Receipt times, prescribed rotation, high-HP target and native damage; no independent physical armor-impact or real-robot accuracy claim.'}


def execute(plan_path):
    plan = read_status(plan_path)
    node = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    port = int((ROOT / 'logs/gestalt/fixed-target-game.log.wsport').read_text().strip())
    if port != plan['port']: raise ValueError('Simulator port changed')
    progress = plan_path.with_name(plan_path.stem.replace('-plan', '') + '-progress.json')

    def run(args, logfile):
        # A Windows background child can retain an inherited pipe after its
        # PowerShell launcher exits. A file avoids waiting for that pipe's EOF.
        with logfile.open('w', encoding='utf-8') as output:
            result = subprocess.run([str(a) for a in args], cwd=ROOT,
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    creationflags=0x08000000 if os.name == 'nt' else 0)
        if result.returncode: raise RuntimeError(str(logfile) + ': subprocess failed')

    for name in plan['trials']:
        if hashes(plan) != plan['sha256']: raise ValueError('Frozen source/executable changed')
        if not name.replace('-', '').isalnum() or len(name) > 48: raise ValueError('Invalid trial name')
        path = plan_path.parent / ('trial-' + name + '.json')
        if path.exists(): raise ValueError('Refusing to overwrite trial ' + name)
        profile = plan['profiles'][name]
        run([node, 'scripts/gestalt_probe.mjs', '--port', port, '--seconds', 1,
             '--command', 'SetAttribute 92011 60000004 10000',
             '--command', 'SetAttribute 92011 10000003 10000', '--output',
             path.with_suffix('').as_posix() + '-reset.jsonl'], path.with_suffix('.reset.log'))
        args = ['powershell.exe', '-NoProfile', '-File', 'scripts/run_gestalt_live.ps1',
                '-Action', 'trial', '-TrialName', name, '-ShotBudget', plan['shots_per_round'],
                '-SettleMs', profile['settle_ms'], '-Prearm', '-HitGoal', profile['hit_goal']]
        if profile['prepare_during_settle']: args.append('-PrepareDuringSettle')
        if profile.get('fixed_center_prediction'): args.append('-FixedCenterPrediction')
        run(args, path.with_suffix('.launcher.log'))
        deadline, last_change, last_frame = time.monotonic() + 720, time.monotonic(), None
        while time.monotonic() < deadline:
            try: raw = read_status(path) if path.exists() else None
            except (OSError, ValueError): raw = None
            if isinstance(raw, dict) and raw:
                if raw.get('error') or raw.get('running') is False:
                    raise RuntimeError(name + ': follower stopped: ' + str(raw.get('error')))
                frame = raw.get('frames')
                if frame != last_frame: last_change, last_frame = time.monotonic(), frame
                trial = raw.get('shot_trial', {})
                write(progress, {'trial': name, 'frames': frame, 'shot_trial': {
                    k: trial.get(k) for k in ('state', 'actual_shots', 'effective_hits')}})
                if trial.get('state') == 'complete': break
            if time.monotonic() - last_change > 30: raise RuntimeError(name + ': no status progress')
            time.sleep(1)
        else: raise RuntimeError(name + ': trial deadline exceeded')
        run([node, 'scripts/gestalt_probe.mjs', '--port', port, '--seconds', 3,
             '--player', 92011, '--expected-omega', 4.87, '--output',
             path.with_suffix('').as_posix() + '-postflight.jsonl'], path.with_suffix('.postflight.log'))
        row = summarize(path)
        if not row['score_checks_passed'] or not audit_native(path, row)['passed']:
            raise RuntimeError(name + ': independent counter check failed')
        write(plan_path.with_name(plan_path.stem.replace('-plan', '') + '-summary.json'), report(plan_path))
        print(name, row['registered_hits'], '/', row['actual_shots'], flush=True)
    write(plan_path.with_name(plan_path.stem.replace('-plan', '') + '-manifest.json'),
          {'plan': str(plan_path), 'end_sha256': hashes(plan), 'summary': report(plan_path)})
    write(progress, {'complete': True, 'trials': plan['trials']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    try:
        if args.execute: execute(args.plan)
        else: print(json.dumps(report(args.plan), ensure_ascii=False, indent=2))
    except Exception as error:
        if args.execute:
            (ROOT / 'logs/gestalt/live.stop').write_text(str(error), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
