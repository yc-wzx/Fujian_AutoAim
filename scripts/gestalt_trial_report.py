#!/usr/bin/env python3
"""Summarize bounded Gestalt shot telemetry, without opening the simulator."""
import argparse
import json
import math
from pathlib import Path
import statistics
import time


def read_status(path):
    # The running controller refreshes the file once per second.
    for attempt in range(5):
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except json.JSONDecodeError:
            if attempt == 4:
                raise
            time.sleep(.05)


def summarize(path):
    report = read_status(path)
    trial = report['shot_trial']
    events = trial['shots']
    actual, budget = trial['actual_shots'], trial['budget']
    issues = []
    if not 0 <= len(events) <= actual <= budget <= 50:
        issues.append('Shot/event/budget counts are inconsistent')
    hits, raw_hits, hp_loss, damage, delays, speeds = 0, 0, 0., 0., [], []
    unscored_contacts = []
    has_hit_counter = bool(events) and all('bullet_hit_counter_delta' in e for e in events)
    hit_threshold_shot = None
    previous_hp_after = None
    first_launch_counter = events[0].get('counter_before') if events else None
    for index, event in enumerate(events, 1):
        loss = event['target_hp_loss']
        if 'target_hp_before' in event and 'target_hp_after' in event:
            recomputed_loss = event['target_hp_before'] - event['target_hp_after']
            if abs(recomputed_loss - loss) > 1e-6:
                issues.append(f'Event {index}: stored loss disagrees with HP subtraction')
            loss = recomputed_loss
            if previous_hp_after is not None and abs(event['target_hp_before'] - previous_hp_after) > 1e-6:
                issues.append(f'Event {index}: target HP is discontinuous')
            previous_hp_after = event['target_hp_after']
        if first_launch_counter is not None and event.get('counter_before') != first_launch_counter + index - 1:
            issues.append(f'Event {index}: cumulative launch counter is discontinuous')
        applied = event['damage_total_delta']
        hit = event.get('bullet_hit_counter_delta', int(loss > 0 and applied > 0))
        if event['actual_total'] != index or hit not in (0, 1):
            issues.append(f'Event {index}: counter sequence is inconsistent')
        if not all(math.isfinite(v) and v >= 0 for v in [loss, applied, hit]):
            issues.append(f'Event {index}: negative/nonfinite score delta')
        effective = int(loss > 0 and applied > 0)
        if effective and hit != 1:
            issues.append(f'Event {index}: damage lacks a matching hit-counter increment')
        if hit == 1 and not effective:
            unscored_contacts.append(index)
        # A final hit can be clamped by the target remaining health.
        clamped = event['target_hp_after'] == 0 and applied >= loss > 0
        if abs(loss - applied) > 1e-6 and not clamped:
            issues.append(f'Event {index}: HP and applied damage disagree')
        if 'target_damage_taken_delta' in event and abs(event['target_damage_taken_delta'] - applied) > 1e-6:
            issues.append(f'Event {index}: source and target damage counters disagree')
        hits += effective
        raw_hits += int(hit)
        hp_loss += loss
        damage += applied
        if hits >= 31 and hit_threshold_shot is None:
            hit_threshold_shot = index
        delays.append(1000 * (event['ack_qpc_s'] - event['request_qpc_s']))
        speeds.append(event['real_speed_cm_s'] / 100)
    terminal = trial['state'] in ('complete', 'target_defeated', 'hit_goal_reached')
    if trial['state'] == 'hit_goal_reached' and (not trial.get('hit_goal') or hits < trial['hit_goal']):
        issues.append('Terminal hit goal lacks enough confirmed effective hits')
    if 'effective_hits' in trial and trial['effective_hits'] != hits:
        issues.append('Controller effective-hit count disagrees with independent scoring')
    if terminal and len(events) != actual:
        issues.append('Terminal trial has unsettled shots')
    settled = len(events)
    return {
        'source': str(path), 'state': trial['state'], 'terminal': terminal,
        'budget': budget, 'actual_shots': actual, 'settled_shots': settled,
        'unsettled_shots': actual - settled,
        'registered_hits': hits,
        'hit_rate': hits / actual if actual and actual == settled and not issues else None,
        'settled_hit_rate': hits / settled if settled and not issues else None,
        'hit_rate_scope': 'Full actual-shot rate is withheld until every launched shot is settled; settled_hit_rate alone is partial telemetry.',
        'bullet_hit_counter_delta': raw_hits, 'non_damaging_hit_counter_shots': unscored_contacts,
        'scoring_basis': 'Effective hits require HP loss (recomputed from endpoints when present) and matching applied/taken damage; non-damaging hit-counter increments are excluded.',
        'evidence': 'BulletHitTotal + HP + both damage counters' if has_hit_counter
                    else 'HP + shooter DamageAppliedTotal; no per-shot hit counter in this legacy report',
        'target_hp_loss': hp_loss, 'damage_applied': damage,
        'ack_interval_median_ms': statistics.median(delays) if delays else None,
        'muzzle_speed_median_mps': statistics.median(speeds) if speeds else None,
        '31_hit_threshold_shot': hit_threshold_shot,
        'parameters': {key: report.get(key) for key in
                       ('bullet_speed_mps', 'sim_delay_ms', 'pitch_bias_deg', 'center_hold', 'phase_fit', 'pitch_hold', 'fire_window_deg', 'diagnostic_pose_reuse', 'settle_ms', 'prearm', 'hit_goal', 'bench_pose', 'prepare_during_settle', 'fixed_center_prediction')},
        'score_checks_passed': not issues, 'issues': issues,
        'scope': 'Simulator diagnostic. Acknowledgements use receipt time; rule damage and muzzle geometry require separate validation.',
    }


def frame_statistics(path, settled_shots):
    frames_path = Path(str(path) + '.frames.jsonl')
    if not frames_path.exists() or settled_shots == 0:
        return None
    processing, ages = [], []
    frame_count = tracking_count = 0
    with frames_path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue  # A running stream can end in a partial final line.
            frame_count += 1
            tracking_count += sample.get('tracker_state') == 'tracking'
            value = sample.get('processing_ms')
            if value is not None and math.isfinite(value):
                processing.append(value)
            value = sample.get('command', {}).get('frame_age_ms')
            if value is not None and math.isfinite(value):
                ages.append(value)
            if len(sample.get('shot_trial', {}).get('shots', [])) >= settled_shots:
                break

    def metrics(values):
        if not values:
            return None
        ordered = sorted(values)
        return {'median': statistics.median(values), 'p95': ordered[math.ceil(.95 * len(ordered)) - 1]}

    return {'sampled_frames': frame_count, 'processing_ms': metrics(processing),
            'frame_age_ms': metrics(ages),
            'tracking_fraction': tracking_count / frame_count if frame_count else None,
            'scope': 'Frames through the last settled shot; telemetry times are not exact launch timestamps.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--frame-stats', action='store_true', help='Read the corresponding streamed frame log for timing diagnostics')
    args = parser.parse_args()
    results = [summarize(path) for path in args.reports]
    if args.frame_stats:
        for path, result in zip(args.reports, results):
            result['frame_stats'] = frame_statistics(path, result['settled_shots'])
    text = json.dumps(results, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    return 0 if all(r['score_checks_passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
