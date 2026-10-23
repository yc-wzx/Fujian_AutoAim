"""Offline evidence/denominator tests; no hardware access."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('shot_timing',
    Path(__file__).resolve().parents[1] / 'scripts/analyze_shot_timing.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def records():
    return {
        (1, 1, 'request_snapshot'): {'frame_ns': 1_000_000_000, 'request_issue_ns': 1_020_000_000},
        (1, 1, 'first_high'): {'tx_ok': True, 'tx_shoot': 1, 'tx_write_complete_ns': 1_025_000_000},
        (1, 2, 'first_high'): {'tx_ok': True, 'tx_shoot': 1, 'tx_write_complete_ns': 2_025_000_000},
        (2, 1, 'first_high'): {'tx_ok': True, 'tx_shoot': 1, 'tx_write_complete_ns': 1_010_000_000},
    }


def observation(**changes):
    return {'run': 1, 'id': 1, 'projectile_id': 'p1', 'tx_to_exit_ms': 315., 'hit': 1,
            'muzzle_speed_mps': 24., 'evidence': 'synthetic test fixture', **changes}


class ShotTimingTests(unittest.TestCase):
    def test_host_writes_never_become_projectiles(self):
        report = MODULE.analyze(records(), [])
        self.assertEqual(report['host_successful_request_writes'], 3)
        self.assertEqual(report['observed_projectiles'], 0)
        self.assertIsNone(report['fully_labeled_observed_sample_hit_rate'])
        self.assertEqual(report['measured_host_write_to_exit_ms'], {'n': 0})
        self.assertEqual(report['host_request_write_interval_ms']['median'], 1000.)
        self.assertEqual(report['image_reference_to_host_write_ms']['median'], 25.)

    def test_partial_hit_labels_withhold_full_sample_rate(self):
        second = observation(); second.update(id=2, projectile_id='p2', hit=None)
        report = MODULE.analyze(records(), [observation(), second])
        self.assertIsNone(report['fully_labeled_observed_sample_hit_rate'])
        self.assertEqual(report['unknown_hit_labels'], 1)
        self.assertEqual(report['labeled_subset_hit_rate'], 1.)

    def test_multiple_projectiles_and_unmatched_run(self):
        second = observation(); second.update(projectile_id='p2', hit=0)
        third = observation(); third.update(run=3, projectile_id='p3', tx_to_exit_ms=999.)
        report = MODULE.analyze(records(), [observation(), second, third])
        self.assertEqual(report['observed_projectiles'], 3)
        self.assertEqual(report['measured_host_write_to_exit_ms']['n'], 2)
        self.assertEqual(report['measured_host_write_to_exit_ms']['median'], 315.)
        self.assertEqual(report['requests_with_multiple_observed_projectiles'][0]['projectiles'], 2)
        self.assertEqual(report['unlinked_observations'][0]['run'], 3)

    def test_failed_write_cannot_link_measured_delay(self):
        data = records(); data[(1, 1, 'first_high')]['tx_ok'] = False
        report = MODULE.analyze(data, [observation()])
        self.assertEqual(report['observed_projectiles'], 1)
        self.assertEqual(report['linked_observed_projectiles'], 0)

    def test_duplicate_and_conflicting_log_records(self):
        row = {'kind': 'first_high', 'run': 1, 'id': 1, 'tx_ok': True}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.log'
            path.write_text('\n'.join('[SHOT_DEBUG] ' + json.dumps(r) for r in [row, row]), encoding='utf-8')
            data, quality = MODULE.read_records([path])
            self.assertEqual(len(data), 1)
            self.assertEqual(quality['identical_records_deduplicated'], 1)
            changed = copy.deepcopy(row); changed['tx_ok'] = False
            with path.open('a', encoding='utf-8') as stream:
                stream.write('\n[SHOT_DEBUG] ' + json.dumps(changed))
            data, quality = MODULE.read_records([path])
            self.assertEqual(data, {})
            self.assertEqual(quality['conflicting_record_keys'], 1)

    def test_csv_rejects_unknown_as_miss_nonfinite_and_duplicate_inflation(self):
        header = 'run,id,projectile_id,tx_to_exit_ms,hit,muzzle_speed_mps,evidence\n'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'input.csv'
            row = '1,1,p1,315,,24,synthetic fixture\n'
            path.write_text(header + row + row, encoding='utf-8')
            data, quality = MODULE.read_observations(path)
            self.assertEqual(len(data), 1)
            self.assertIsNone(data[0]['hit'])
            self.assertEqual(quality['identical_projectile_rows_deduplicated'], 1)
            path.write_text(header + row + row.replace('315', '320'), encoding='utf-8')
            data, quality = MODULE.read_observations(path)
            self.assertEqual(data, [])
            self.assertEqual(quality['conflicting_projectile_ids_excluded'], [[1, 'p1']])
            for invalid in ['nan', 'inf', '-1']:
                path.write_text(header + row.replace('315', invalid), encoding='utf-8')
                with self.assertRaises(ValueError):
                    MODULE.read_observations(path)


if __name__ == '__main__':
    unittest.main()
