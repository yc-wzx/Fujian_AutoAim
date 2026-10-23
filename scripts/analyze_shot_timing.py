#!/usr/bin/env python3
"""Offline request timing and independently observed projectile review.

No camera, serial, network, robot startup, configuration write, or delay tuning.
Host write completion is not muzzle exit. Measurements are never inferred from
request counts, feeder feedback, current thresholds, or the estimated 320 ms.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    # Nearest-rank percentiles; report sample count rather than imply precision.
    return {'n': len(values), 'min': values[0], 'median': statistics.median(values),
            'mean': statistics.mean(values), 'max': values[-1],
            'p05': values[max(0, math.ceil(.05 * len(values)) - 1)],
            'p95': values[math.ceil(.95 * len(values)) - 1]}


def read_records(paths):
    grouped = defaultdict(list)
    quality = Counter()
    for path in paths:
        with path.open(encoding='utf-8-sig', errors='replace') as stream:
            for line in stream:
                if '[SHOT_DEBUG] ' not in line:
                    continue
                try:
                    item = json.loads(line.split('[SHOT_DEBUG] ', 1)[1])
                except ValueError:
                    quality['parse_errors'] += 1
                    continue
                if not isinstance(item, dict):
                    quality['parse_errors'] += 1
                    continue
                for name in ('queue_dropped_total', 'sink_errors_total'):
                    value = item.get(name)
                    if finite(value):
                        quality[name + '_max'] = max(quality[name + '_max'], value)
                if item.get('kind') not in ('request_snapshot', 'first_high'):
                    continue
                if any(type(item.get(k)) is not int or item[k] <= 0 for k in ('run', 'id')):
                    quality['invalid_record_keys'] += 1
                    continue
                grouped[(item['run'], item['id'], item['kind'])].append(item)
    records = {}
    for key, items in grouped.items():
        if any(item != items[0] for item in items[1:]):
            quality['conflicting_record_keys'] += 1
            continue
        records[key] = items[0]
        quality['identical_records_deduplicated'] += len(items) - 1
    return records, dict(quality)


def read_observations(path):
    """One independently confirmed exiting projectile per row; blanks are unknown."""
    grouped = defaultdict(list)
    required = {'run', 'id', 'projectile_id', 'tx_to_exit_ms', 'hit', 'muzzle_speed_mps', 'evidence'}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise ValueError('Observation CSV needs columns: ' + ','.join(sorted(required)))
        for number, row in enumerate(reader, 2):
            try:
                item = {k: (row[k] or '').strip() for k in required}
                item['run'], item['id'] = int(item['run']), int(item['id'])
                if min(item['run'], item['id']) <= 0 or not item['projectile_id'] or not item['evidence']:
                    raise ValueError('positive run/id, projectile_id and evidence are required')
                if item['hit'] not in ('', '0', '1'):
                    raise ValueError('hit must be blank, 0 or 1')
                item['hit'] = int(item['hit']) if item['hit'] else None
                for field in ('tx_to_exit_ms', 'muzzle_speed_mps'):
                    item[field] = float(item[field]) if item[field] else None
                    value = item[field]
                    if value is not None and (not math.isfinite(value) or value < 0 or
                                              (field == 'muzzle_speed_mps' and value == 0)):
                        raise ValueError(field + ' must be finite and nonnegative (speed > 0)')
                grouped[(item['run'], item['projectile_id'])].append(item)
            except (TypeError, ValueError) as error:
                raise ValueError(f'CSV row {number}: {error}') from error
    result, conflicts, duplicates = [], [], 0
    for key, items in grouped.items():
        if any(item != items[0] for item in items[1:]):
            conflicts.append(list(key))
            continue
        result.append(items[0])
        duplicates += len(items) - 1
    return result, {'conflicting_projectile_ids_excluded': conflicts,
                    'identical_projectile_rows_deduplicated': duplicates}


def analyze(records, observations):
    requests = {(run, identity): row for (run, identity, kind), row in records.items()
                if kind == 'request_snapshot'}
    writes = {(run, identity): row for (run, identity, kind), row in records.items()
              if kind == 'first_high' and row.get('tx_ok') is True and row.get('tx_shoot') == 1
              and finite(row.get('tx_write_complete_ns')) and row['tx_write_complete_ns'] > 0}
    per_run = defaultdict(list)
    frame_delays, issue_delays = [], []
    for key, write in writes.items():
        timestamp = write['tx_write_complete_ns']
        per_run[key[0]].append(timestamp)
        request = requests.get(key, {})
        for field, output in (('frame_ns', frame_delays), ('request_issue_ns', issue_delays)):
            start = request.get(field)
            if finite(start) and timestamp >= start:
                output.append((timestamp - start) / 1e6)
    intervals = [right - left for times in per_run.values()
                 for left, right in zip(sorted(times), sorted(times)[1:])]
    linked, unlinked, measured_delays = [], [], []
    projectiles_per_request = Counter()
    for item in observations:
        key = (item['run'], item['id'])
        if key in writes:
            linked.append(item)
            projectiles_per_request[key] += 1
            if item['tx_to_exit_ms'] is not None:
                measured_delays.append(item['tx_to_exit_ms'])
        else:
            unlinked.append({'run': item['run'], 'id': item['id'], 'projectile_id': item['projectile_id']})
    labeled = [item for item in observations if item['hit'] is not None]
    hits = sum(item['hit'] for item in labeled)
    return {
        'request_snapshots': len(requests), 'host_successful_request_writes': len(writes),
        'host_request_write_interval_ms': distribution([v / 1e6 for v in intervals]),
        'image_reference_to_host_write_ms': distribution(frame_delays),
        'request_issue_to_host_write_ms': distribution(issue_delays),
        'observed_projectiles': len(observations), 'linked_observed_projectiles': len(linked),
        'unlinked_observations': unlinked,
        'requests_with_multiple_observed_projectiles': [
            {'run': key[0], 'id': key[1], 'projectiles': count}
            for key, count in projectiles_per_request.items() if count > 1],
        'measured_host_write_to_exit_ms': distribution(measured_delays),
        'measured_muzzle_speed_mps': distribution([
            item['muzzle_speed_mps'] for item in observations if item['muzzle_speed_mps'] is not None]),
        'known_hit_labels': len(labeled), 'unknown_hit_labels': len(observations) - len(labeled),
        'observed_effective_hits': hits,
        'fully_labeled_observed_sample_hit_rate': hits / len(observations)
            if observations and len(labeled) == len(observations) else None,
        'labeled_subset_hit_rate': hits / len(labeled) if labeled else None,
        'limitations': [
            'Observed sample completeness and evidence authenticity require human review; no whole-round or real-robot forecast is inferred.',
            'CSV rows must describe independently confirmed projectiles; a request, current threshold or feeder cycle is not a projectile.',
            'Provided tx_to_exit_ms must be independently measured from the same host-write reference; different clocks need documented synchronization.',
            'Host write completion is neither wire transmission completion nor lower-controller receipt nor muzzle exit.',
            'Unknown hit labels are not misses; no full observed-sample hit rate is given until all labels are known.',
            'Unlinked measurements are retained as observations but excluded from request-linked delay statistics.',
            'Image references are host receive times with configured offset, not verified physical exposure centers.',
            'Debug collection can change timing. Request cadence is not measured projectile cadence.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    inputs = args.logs + ([args.observations] if args.observations else [])
    log_root = Path(__file__).resolve().parents[1] / 'logs'
    if not output.is_relative_to(log_root.resolve()) or output in [p.resolve() for p in inputs]:
        parser.error('Output must be inside logs/ and must not overwrite an input')
    try:
        records, record_quality = read_records(args.logs)
        observations, observation_quality = read_observations(args.observations) if args.observations else ([], {})
        report = analyze(records, observations)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report.update(record_quality=record_quality, observation_quality=observation_quality,
                  source_sha256={str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('request_snapshots', 'host_successful_request_writes',
                     'observed_projectiles', 'measured_host_write_to_exit_ms',
                     'fully_labeled_observed_sample_hit_rate')}, indent=2))
    if not records:
        parser.exit(2, 'No usable SHOT_DEBUG request/write records; no hardware timing claim is available.\n')


if __name__ == '__main__':
    main()
