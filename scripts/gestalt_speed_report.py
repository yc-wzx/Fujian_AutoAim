#!/usr/bin/env python3
"""Offline paired speed analysis and independent native-counter audit."""
import argparse
import json
from pathlib import Path
import statistics

from gestalt_series_report import series_report, wilson
from gestalt_trial_report import read_status


def native_snapshot(path):
    maps, shots = {}, {}
    for line in path.open(encoding='utf-8-sig'):
        row = json.loads(line)
        for item in row.get('packet', {}).get('params', {}).get('watch_attribute_maps_results', []):
            mid, attributes = item['attribute_map_id'], item.get('attributes', {})
            maps.setdefault(mid, {}).update(attributes)
            if '63000002' in attributes:
                shots.setdefault(mid, []).append(attributes['63000002'])
    players = {}
    for pid in (0, 92011):
        candidates = [(mid, values) for mid, values in maps.items()
                      if values.get('10000035') == pid and values.get('60000002') == 1003]
        if len(candidates) != 1:
            raise ValueError(f'{path}: expected one infantry player {pid}')
        mid, values = candidates[0]
        players[pid] = {'map': mid, 'attributes': values,
                        'shot_counter_values': sorted(set(shots.get(mid, [])))}
    return players


def audit_native(report_path, result):
    base = report_path.with_suffix('')
    before = native_snapshot(Path(str(base) + '-preflight.jsonl'))
    after = native_snapshot(Path(str(base) + '-postflight.jsonl'))
    checks = {}
    for pid in (0, 92011):
        checks[f'player_{pid}_same_map'] = before[pid]['map'] == after[pid]['map']
        checks[f'player_{pid}_zero_ammo'] = all(
            after[pid]['attributes'].get(key) == 0 for key in ('10000031', '10000033'))
    own_before, own_after = before[0]['attributes'], after[0]['attributes']
    target_before, target_after = before[92011]['attributes'], after[92011]['attributes']
    deltas = {
        'actual_launches': own_after['63000002'] - own_before['63000002'],
        'raw_hits': own_after['63000003'] - own_before['63000003'],
        'source_damage': own_after['63000000'] - own_before['63000000'],
        'target_damage': target_after['63000001'] - target_before['63000001'],
        'target_hp_loss': target_before['10000003'] - target_after['10000003'],
    }
    checks['actual_launches'] = deltas['actual_launches'] == result['actual_shots']
    checks['raw_hits'] = deltas['raw_hits'] == result['bullet_hit_counter_delta']
    checks['source_damage'] = deltas['source_damage'] == result['damage_applied']
    checks['target_damage'] = deltas['target_damage'] == result['damage_applied']
    checks['target_hp_loss'] = deltas['target_hp_loss'] == result['target_hp_loss']
    checks['no_extra_launches_in_postflight'] = len(after[0]['shot_counter_values']) == 1
    return {'passed': all(checks.values()), 'checks': checks, 'native_deltas': deltas,
            'postflight_shot_values': after[0]['shot_counter_values'],
            'scope': 'Separate loopback observer for 3 s after completion, before any HP reset. Receipt-time telemetry, not exact physical launch/impact times.'}


def speed_report(plan_path):
    plan = read_status(plan_path)
    report = series_report(plan_path)
    administrative_stops = {e['trial']: e for e in report['protocol_events']
                            if e.get('event') == 'administrative_stop_after_completed_31_hit_endpoint'}
    for result in report['rounds']:
        profile = plan['profiles'][result['trial']]
        result['profile'] = profile
        for key in ('settle_ms', 'prearm', 'hit_goal', 'bench_pose'):
            if result['parameters'].get(key) != profile[key]:
                report['issues'].append(f'{result["trial"]}: changed {key}')
        path = plan_path.parent / f'trial-{result["trial"]}.json'
        raw = read_status(path)
        stop = administrative_stops.get(result['trial'])
        admin_eligible = bool(stop and raw.get('running') is False and not raw.get('error') and
            result['score_checks_passed'] and result['actual_shots'] == result['settled_shots'] == stop['actual_shots'] and
            result['31_hit_threshold_shot'] is not None)
        result['administratively_stopped_after_endpoint'] = admin_eligible
        result['speed_endpoint_eligible'] = result['state'] == 'complete' or admin_eligible
        if result['speed_endpoint_eligible']:
            try:
                result['independent_native_audit'] = audit_native(path, result)
                if not result['independent_native_audit']['passed']:
                    report['issues'].append(f'{result["trial"]}: native postflight disagrees')
            except (OSError, KeyError, ValueError) as error:
                report['issues'].append(str(error))
    report['complete_and_valid'] = report['complete_and_valid'] and not report['issues']
    report['original_50shot_protocol_complete'] = report['complete_and_valid']
    report['speed_endpoints_complete_and_audited'] = bool(len(report['rounds']) == plan['rounds'] and
        not report['issues'] and all(r['speed_endpoint_eligible'] and r['31_hit_threshold_shot'] is not None
                                    for r in report['rounds']))
    groups = {}
    for mode in ('slow', 'fast'):
        rows = [r for r in report['rounds'] if r['profile']['mode'] == mode and r['speed_endpoint_eligible']]
        hits = sum(r['registered_hits'] for r in rows)
        shots = sum(r['settled_shots'] for r in rows)
        threshold_times = [r['receipt_timing']['first_request_to_31st_effective_hit_ack_s'] for r in rows
                           if r['receipt_timing'] and r['receipt_timing']['first_request_to_31st_effective_hit_ack_s'] is not None]
        groups[mode] = {'rounds': len(rows), 'hits': hits, 'shots': shots,
                        'hit_rate': hits / shots if shots else None, 'wilson_95': wilson(hits, shots),
                        '31_hit_rounds': len(threshold_times),
                        '31_hit_receipt_times_s': threshold_times,
                        'median_31_hit_receipt_time_s': statistics.median(threshold_times) if threshold_times else None}
    report['groups'] = groups
    slow_time = groups['slow']['median_31_hit_receipt_time_s']
    fast_time = groups['fast']['median_31_hit_receipt_time_s']
    ratio = fast_time / slow_time if slow_time and fast_time else None
    report['fast_to_slow_time_ratio'] = ratio
    report['candidate_criteria'] = plan['candidate_criteria']
    report['fast_adoptable_in_simulator'] = bool(report['speed_endpoints_complete_and_audited'] and
        groups['fast']['rounds'] == groups['fast']['31_hit_rounds'] == 2 and
        groups['fast']['shots'] == 100 and
        groups['fast']['hit_rate'] >= .80 and ratio is not None and ratio <= .85)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = speed_report(args.plan)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('original_50shot_protocol_complete', 'speed_endpoints_complete_and_audited', 'issues', 'groups',
          'fast_to_slow_time_ratio', 'fast_adoptable_in_simulator')}, indent=2))
    return 0 if result['speed_endpoints_complete_and_audited'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
