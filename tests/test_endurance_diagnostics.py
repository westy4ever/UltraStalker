# -*- coding: utf-8 -*-
import unittest

from ..core.endurance import EnduranceMonitor
from ..core import runtime_log


class _SequenceSampler(object):
    def __init__(self, rows):
        self.rows = list(rows)
        self.index = 0
    def __call__(self):
        row = self.rows[min(self.index, len(self.rows) - 1)]
        self.index += 1
        return dict(row)


class EnduranceDiagnosticsTests(unittest.TestCase):
    def test_stable_resources_remain_healthy(self):
        sampler = _SequenceSampler([
            {'rss_kb': 100000, 'vmsize_kb': 200000, 'fds': 20, 'threads': 5},
            {'rss_kb': 103000, 'vmsize_kb': 204000, 'fds': 22, 'threads': 6},
        ])
        monitor = EnduranceMonitor(sampler=sampler)
        monitor.observe('start')
        report = monitor.observe('cycle')
        self.assertTrue(report['healthy'])
        self.assertEqual(report['samples'], 2)
        self.assertEqual(report['delta']['fds'], 2)

    def test_rss_growth_is_flagged(self):
        sampler = _SequenceSampler([
            {'rss_kb': 100000, 'fds': 20, 'threads': 5},
            {'rss_kb': 210000, 'fds': 20, 'threads': 5},
        ])
        monitor = EnduranceMonitor(sampler=sampler)
        monitor.observe('start')
        report = monitor.observe('after-zapping')
        self.assertFalse(report['healthy'])
        self.assertTrue(any(item.startswith('rss_kb+') for item in report['warnings']))

    def test_fd_and_thread_growth_are_flagged(self):
        sampler = _SequenceSampler([
            {'rss_kb': 100000, 'fds': 10, 'threads': 3},
            {'rss_kb': 100000, 'fds': 50, 'threads': 14},
        ])
        monitor = EnduranceMonitor(sampler=sampler)
        monitor.observe('start')
        report = monitor.observe('after-cycles')
        self.assertFalse(report['healthy'])
        self.assertTrue(any(item.startswith('fds+') for item in report['warnings']))
        self.assertTrue(any(item.startswith('threads+') for item in report['warnings']))

    def test_peak_is_preserved_after_resources_drop(self):
        sampler = _SequenceSampler([
            {'rss_kb': 100000, 'fds': 10, 'threads': 3},
            {'rss_kb': 130000, 'fds': 18, 'threads': 4},
            {'rss_kb': 105000, 'fds': 11, 'threads': 3},
        ])
        monitor = EnduranceMonitor(sampler=sampler)
        monitor.observe('start')
        monitor.observe('peak')
        report = monitor.observe('settled')
        self.assertEqual(report['peak']['rss_kb'], 130000)
        self.assertEqual(report['peak']['fds'], 18)
        self.assertTrue(report['healthy'])


class PeriodicSoakDiagnosticsTests(unittest.TestCase):
    def test_periodic_row_contains_resource_deltas_without_private_data(self):
        original=runtime_log.ENDURANCE
        sampler=_SequenceSampler([
            {'rss_kb':100000,'vmsize_kb':200000,'fds':20,'threads':5},
            {'rss_kb':104000,'vmsize_kb':203000,'fds':22,'threads':6},
        ])
        monitor=EnduranceMonitor(sampler=sampler)
        monitor.observe('start')
        try:
            runtime_log.ENDURANCE=monitor
            row=runtime_log._endurance_sample_row('periodic')
        finally:
            runtime_log.ENDURANCE=original
        self.assertEqual(row['event'],'soak_resource_sample')
        self.assertEqual(row['rss_delta_kb'],4000)
        self.assertEqual(row['fd_delta'],2)
        self.assertEqual(row['thread_delta'],1)
        self.assertNotIn('portal',row)
        self.assertNotIn('url',row)

    def test_periodic_sampler_is_rate_limited(self):
        old_last=runtime_log._LAST_ENDURANCE_SAMPLE
        old_write=runtime_log._write_row
        rows=[]
        try:
            runtime_log._LAST_ENDURANCE_SAMPLE=0.0
            runtime_log._write_row=lambda row: rows.append(dict(row))
            self.assertTrue(runtime_log._maybe_write_endurance_sample(now=100.0))
            self.assertFalse(runtime_log._maybe_write_endurance_sample(now=120.0))
            self.assertTrue(runtime_log._maybe_write_endurance_sample(now=161.0))
        finally:
            runtime_log._LAST_ENDURANCE_SAMPLE=old_last
            runtime_log._write_row=old_write
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row.get('event')=='soak_resource_sample' for row in rows))


if __name__ == '__main__':
    unittest.main()
