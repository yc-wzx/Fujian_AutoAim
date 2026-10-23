#!/usr/bin/env python3
"""Read-only recount of the recorded Gestalt repeat/speed studies.

Uses only the standard library, original per-shot records, and original WS
attribute packets. Deliberately does not import the existing report helpers.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path


FIELDS = {
    'player': '10000035', 'team': '10000036', 'class': '60000002',
    'hp': '10000003', 'fired': '63000002', 'raw_hits': '63000003',
    'damage': '63000000', 'received': '63000001',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logs', type=Path, default=Path('logs/gestalt'))
    parser.add_argument('--plan', type=Path, help='Audit this frozen fixed-budget plan instead of the original recorded studies')
    args = parser.parse_args()
    folder = args.logs
    sources, issues, rows, trials = {}, [], [], []

    def check(condition, message):
        if not condition:
            issues.append(message)

    def read(path):
        data = path.read_bytes()
        sources[str(path)] = hashlib.sha256(data).hexdigest()
        return data.decode('utf-8-sig')

    def snapshot(name):
        maps = {}
        observed = {}
        for line in read(folder / (name + '.jsonl')).splitlines():
            packet = json.loads(line).get('packet', {})
            for item in packet.get('params', {}).get('watch_attribute_maps_results', []):
                identity = item['attribute_map_id']
                attrs = item.get('attributes', {})
                maps.setdefault(identity, {}).update(attrs)
                for field, key in FIELDS.items():
                    if key in attrs:
                        observed.setdefault((identity, field), set()).add(attrs[key])
        result = {}
        for identity, attrs in maps.items():
            if attrs.get(FIELDS['class']) != 1003:
                continue
            player = attrs.get(FIELDS['player'])
            check(player not in result, f'{name}: duplicate infantry player {player}')
            result[player] = {'map': identity, **{
                field: attrs.get(key) for field, key in FIELDS.items()
            }}
            for field in ('fired', 'raw_hits', 'damage', 'received'):
                check(len(observed.get((identity, field), set())) <= 1,
                      f'{name}: {player} {field} changed inside boundary observation')
        check(set(result) == {0, 92011}, f'{name}: unexpected player identities')
        check(result[0]['team'] == 1 and result[92011]['team'] == 0,
              f'{name}: unexpected teams')
        return result

    plan = json.loads(read(args.plan)) if args.plan else None
    if plan:
        folder = args.plan.parent
        names = plan['trials']
        check(bool(names) and len(set(names)) == len(names), 'Plan has missing/duplicate trial names')
    else:
        names = [f'repeat-fixed-{i:02}' for i in range(1, 6)]
        names += ['speed-slow-01', 'speed-fast-01', 'speed-slow-02',
                  'speed-fast-02', 'speed-goal-31']
    launches = []
    for index, name in enumerate(names):
        report = json.loads(read(folder / ('trial-' + name + '.json')))
        trial = report['shot_trial']
        events = trial['shots']  # Count one final list, never its per-frame copies.
        actual = trial['actual_shots']
        check(len(events) == actual, f'{name}: unsettled or excess shot records')
        if plan:
            check(trial['state'] == 'complete' and actual == plan['shots_per_round'],
                  f'{name}: fixed-budget plan incomplete')
        pre = snapshot('trial-' + name + '-preflight')
        post_name = ('trial-' + names[index + 1] + '-preflight'
                     if name.startswith('repeat-fixed-')
                     else 'trial-' + name + '-postflight')
        post = snapshot(post_name)
        hits, hp_loss, damage, received, raw_hits = 0, 0, 0, 0, 0
        misses = []
        for number, event in enumerate(events, 1):
            loss = event['target_hp_before'] - event['target_hp_after']
            applied = event['damage_total_delta']
            taken = event['target_damage_taken_delta']
            raw = event['bullet_hit_counter_delta']
            valid_hit = (loss, applied, taken, raw) == (20, 20, 20, 1)
            valid_miss = (loss, applied, taken, raw) == (0, 0, 0, 0)
            check(valid_hit or valid_miss, f'{name} shot {number}: score disagreement')
            check(loss == event['target_hp_loss'], f'{name} shot {number}: HP subtraction mismatch')
            check(event['actual_total'] == number, f'{name} shot {number}: local index mismatch')
            launch = event['counter_before']
            check(launch == pre[0]['fired'] + number - 1,
                  f'{name} shot {number}: cumulative launch index mismatch')
            launches.append(launch)
            if number > 1:
                check(event['target_hp_before'] == events[number - 2]['target_hp_after'],
                      f'{name} shot {number}: unexplained HP discontinuity')
            check(event['ack_qpc_s'] >= event['request_qpc_s'],
                  f'{name} shot {number}: acknowledgement precedes request')
            hits += int(valid_hit)
            if not valid_hit:
                misses.append(number)
            hp_loss += loss
            damage += applied
            received += taken
            raw_hits += raw
            rows.append({'trial': name, 'shot': number, 'counter_before': launch,
                         'hp_before': event['target_hp_before'],
                         'hp_after': event['target_hp_after'], 'hp_loss_recomputed': loss,
                         'source_damage': applied, 'target_damage': taken,
                         'raw_hits': raw, 'effective_hit': int(valid_hit)})
        native = {
            'shots': post[0]['fired'] - pre[0]['fired'],
            'hits': post[0]['raw_hits'] - pre[0]['raw_hits'],
            'source_damage': post[0]['damage'] - pre[0]['damage'],
            'target_damage': post[92011]['received'] - pre[92011]['received'],
        }
        check(native == {'shots': actual, 'hits': hits,
                         'source_damage': damage, 'target_damage': received},
              f'{name}: independent native cumulative counters disagree')
        check(raw_hits == hits and hp_loss == damage == received == hits * 20,
              f'{name}: per-shot score totals disagree')
        if not name.startswith('repeat-fixed-'):
            check(pre[92011]['hp'] - post[92011]['hp'] == hp_loss,
                  f'{name}: native HP endpoints disagree')
        trials.append({'trial': name, 'state': trial['state'], 'actual_shots': actual,
                       'settled_shots': len(events), 'effective_hits': hits,
                       'hit_rate': hits / actual if actual else None,
                       'source_damage': damage, 'hp_loss_recomputed': hp_loss,
                       'miss_shot_numbers': misses, 'native_counter_deltas': native,
                       'native_start': pre, 'native_end': post,
                       'native_end_source': post_name,
                       'hp_reset_before_native_end': name.startswith('repeat-fixed-')})

    # The retained failed fifth-round acquisition also contains no actual shot.
    if not plan:
        acquisition = json.loads(read(folder / 'trial-repeat-fixed-05-acquisition.json'))
        check(acquisition['shot_trial']['actual_shots'] == 0 and
              not acquisition['shot_trial']['shots'], 'Failed acquisition has uncounted shots')
        acquisition_boundary = snapshot('trial-repeat-fixed-05-acquisition-preflight')
        check(acquisition_boundary[0]['fired'] == 200,
              'Failed acquisition boundary has unexpected cumulative launches')

    first, last = trials[0]['native_start'], trials[-1]['native_end']
    start, end = first[0]['fired'], last[0]['fired']
    check(sorted(launches) == list(range(int(start), int(end))),
          'Global launch coverage contains duplicates, gaps, or unreported shots')
    cohorts = {}
    selections = {
        'repeat_fixed': trials[:5],
        'speed_slow_includes_administrative_49_shot_round': [trials[5], trials[7]],
        'speed_fast': [trials[6], trials[8]],
        'goal_stopped_excluded_from_accuracy_cohorts': [trials[9]],
    } if not plan else {
        mode: [t for t in trials if plan['profiles'][t['trial']]['mode'] == mode]
        for mode in sorted({p['mode'] for p in plan['profiles'].values()})
    }
    for label, selected in selections.items():
        shots = sum(t['actual_shots'] for t in selected)
        hits = sum(t['effective_hits'] for t in selected)
        cohorts[label] = {'shots': shots, 'hits': hits, 'hit_rate': hits / shots,
                          'source_damage': sum(t['source_damage'] for t in selected)}
    result = {
        'passed': not issues, 'issues': issues, 'cohorts': cohorts, 'trials': trials,
        'global_coverage': {'first_counter_before': start, 'last_counter_after': end,
                            'actual_shots': len(launches), 'unique_launch_indices': len(set(launches)),
                            'hits': sum(t['effective_hits'] for t in trials),
                            'raw_hit_counter_before': first[0]['raw_hits'],
                            'raw_hit_counter_after': last[0]['raw_hits']},
        'source_sha256': sources,
        'limitations': [
            'WS packets and shot events are two readers of the same game engine, not independent impact ground truth.',
            'No independent per-projectile collision/video/official armor-trigger validation is established.',
            'Native damage is 20 per effective hit; PDF damage is 10. Each matching event counts once.',
            'Firing is conditional on the simulator helper qualifying an aiming window.',
            'High-HP target and prescribed rotational transforms differ from a complete official match.',
            'Simulated compensation setting is 200 ms; user estimated real command-to-launch delay is 320 ms.',
            *(['The slow comparison is 99 rather than the planned 100 shots; do not call the original 4x50 protocol complete.',
               'The goal-stopped run and zero-shot acquisition attempt are not fixed-budget accuracy samples.']
              if not plan else ['Only this frozen plan is included; pilots, other iterations and goal-stopped runs are excluded.']),
            'These observations do not provide an estimate of the real robot new hit rate.',
        ],
    }
    stem = args.plan.stem.replace('-plan', '') + '-count-audit' if plan else 'independent-count-audit'
    destination = folder / (stem + '.json')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (folder / (stem + '.csv')).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({key: result[key] for key in ('passed', 'issues', 'cohorts', 'global_coverage')}, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
