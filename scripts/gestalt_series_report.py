#!/usr/bin/env python3
"""Audit pre-registered Gestalt repeats offline; never connect to robot/simulator IO."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from gestalt_trial_report import read_status, summarize


def wilson(hits, count):
    if not count:
        return None
    z = statistics.NormalDist().inv_cdf(.975)
    p = hits / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    half = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [center - half, center + half]


def binomial_threshold(probability, count=50, required=31):
    return sum(math.comb(count, k) * probability**k * (1 - probability)**(count - k)
               for k in range(required, count + 1))


def receipt_timing(events):
    if not events:
        return None
    acknowledgements = [e['ack_qpc_s'] for e in events]
    gaps = [b - a for a, b in zip(acknowledgements, acknowledgements[1:])]
    effective = 0
    threshold_elapsed = None
    for event in events:
        effective += event['target_hp_loss'] > 0 and event['damage_total_delta'] > 0
        if effective == 31 and threshold_elapsed is None:
            threshold_elapsed = event['ack_qpc_s'] - events[0]['request_qpc_s']
    span = acknowledgements[-1] - events[0]['request_qpc_s']
    return {'first_request_to_last_ack_s': span,
            'median_ack_gap_s': statistics.median(gaps) if gaps else None,
            'p95_ack_gap_s': sorted(gaps)[math.ceil(.95 * len(gaps)) - 1] if gaps else None,
            'first_request_to_31st_effective_hit_ack_s': threshold_elapsed,
            'effective_hits_per_observed_s': effective / span if span > 0 else None,
            'scope': 'Local receipt timestamps. The 31st-hit time ends at its launch acknowledgement, identified by later damage; it is not an exact impact or full competition time.'}


def series_report(plan_path):
    plan = read_status(plan_path)
    root = Path(__file__).resolve().parents[1]
    issues, rounds, pending, changed_files = [], [], [], []
    for relative, expected in plan['sha256'].items():
        path = root / relative
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed_files.append(relative)
    for name in plan['trials']:
        path = plan_path.parent / f'trial-{name}.json'
        if not path.exists():
            pending.append(name)
            continue
        raw = read_status(path)
        if 'shot_trial' not in raw:
            issues.append(f'{name}: no shot telemetry, error={raw.get("error")}')
            continue
        result = summarize(path)
        result['trial'] = name
        result['receipt_timing'] = receipt_timing(raw['shot_trial']['shots'])
        if raw.get('error'):
            issues.append(f'{name}: {raw["error"]}')
        if result['state'] != 'complete':
            pending.append(name)
        elif result['actual_shots'] != plan['shots_per_round']:
            issues.append(f'{name}: incomplete registered shot budget')
        if not result['score_checks_passed']:
            issues.extend(f'{name}: {problem}' for problem in result['issues'])
        for key in ('bullet_speed_mps', 'sim_delay_ms', 'pitch_bias_deg',
                    'center_hold', 'fire_window_deg'):
            if result['parameters'][key] != plan['settings'][key]:
                issues.append(f'{name}: changed parameter {key}')
        outcomes = [int(e['target_hp_loss'] > 0 and e['damage_total_delta'] > 0)
                    for e in raw['shot_trial']['shots']]
        streak = maximum = 0
        for hit in outcomes:
            streak = 0 if hit else streak + 1
            maximum = max(maximum, streak)
        result['maximum_consecutive_misses'] = maximum
        result['rule_virtual_remaining_hp_after_diagnostic'] = max(0, 310 - 10 * sum(outcomes))
        result['native_start_hp'] = raw['shot_trial']['shots'][0]['target_hp_before'] if outcomes else None
        result['rule_31_hit_threshold_reached'] = sum(outcomes) >= 31
        result['native_per_hit_damage_values'] = sorted({e['damage_total_delta']
            for e in raw['shot_trial']['shots'] if e['target_hp_loss'] > 0})
        rounds.append(result)
    complete = not pending and len(rounds) == plan['rounds'] and not issues
    completed = [r for r in rounds if r['state'] == 'complete' and r['score_checks_passed']]
    hits = sum(r['registered_hits'] for r in completed)
    count = sum(r['settled_shots'] for r in completed)
    rates = [r['hit_rate'] for r in completed]
    thresholds = [r['31_hit_threshold_shot'] for r in completed
                  if r['31_hit_threshold_shot'] is not None]
    reached = sum(r['rule_31_hit_threshold_reached'] for r in completed)
    prefix = plan_path.stem.removesuffix('-plan')
    protocol_path = plan_path.parent / f'{prefix}-protocol-events.jsonl'
    protocol_events = [json.loads(line) for line in protocol_path.read_text(encoding='utf-8').splitlines()
                       if line.strip()] if protocol_path.exists() else []
    context_path = plan_path.parent / f'{prefix}-real-context.json'
    real_context = read_status(context_path) if context_path.exists() else plan['real_robot_context']
    return {
        'plan': str(plan_path), 'complete_and_valid': complete, 'issues': issues,
        'current_files_match_plan': not changed_files,
        'current_file_mismatches': changed_files,
        'file_check_scope': 'Compare files at report-generation time with the registered plan. Later source edits do not invalidate historical scoring; preserve the end-of-series manifest to audit the tested versions.',
        'protocol_events': protocol_events,
        'pending': pending, 'rounds': rounds,
        'completed_rounds': len(completed), 'effective_hits': hits, 'settled_shots': count,
        'pooled_hit_rate': hits / count if count else None,
        'pooled_wilson_95': wilson(hits, count),
        'interval_scope': 'Descriptive binomial interval, assumes independent constant-probability shots. Serial correlation and scene mismatch are not covered. Never a real-robot interval.',
        'round_rate_range': [min(rates), max(rates)] if rates else None,
        'round_rate_sample_sd': statistics.stdev(rates) if len(rates) > 1 else None,
        'rule_31_hit_threshold_rounds': reached,
        'rule_31_hit_threshold_fraction': reached / len(completed) if completed else None,
        'threshold_rounds_wilson_95': wilson(reached, len(completed)),
        '31st_hit_shot_range': [min(thresholds), max(thresholds)] if thresholds else None,
        'historical_48_of_50_included': False,
        'historical_scope': 'Previous exploratory/validation rounds are separate from this prospective series.',
        'conditional_independent_shot_model': [
            {'assumed_hit_probability': p, 'expected_hits_in_50': 50 * p,
             'probability_of_at_least_31_hits_in_50': binomial_threshold(p)}
            for p in (.30, .50, .60, .62, .65, .70, .75, .80, .90)],
        'model_scope': 'A mathematical sensitivity table, not measured real-robot performance; correlated misses can change the threshold probability.',
        'real_robot_context': real_context,
        'real_robot_new_hit_rate_estimate': None,
        'real_planning_baseline': 'Use the reported historical 30% as a provisional baseline until the same real robot and new code are measured. This is not a forecast that the new code will score 30%.',
        'transfer_limit': 'No paired same-version simulator/real trials, measured shot-delay distribution, or real shot dispersion. The reported historical 30% is in the same nominal range/rotation conditions, has unknown sample size, and is the only supplied real hit-rate observation.',
        'references': {
            'wilson': 'https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm',
            'simulation_gap': 'https://xbpeng.github.io/projects/SimToReal/'},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = series_report(args.plan)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if result['complete_and_valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
