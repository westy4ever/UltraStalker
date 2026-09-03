# -*- coding: utf-8 -*-
import unittest

from ..run_receiver_soak_check import evaluate


def sample(rss=0, fds=0, threads=0, peak=0):
    return {
        'event': 'soak_resource_sample',
        'rss_delta_kb': rss,
        'fd_delta': fds,
        'thread_delta': threads,
        'rss_peak_kb': peak,
    }


class ReceiverSoakCheckTests(unittest.TestCase):
    def test_stable_run_passes(self):
        rows=[sample(1000+i*100, i, 0) for i in range(6)]
        self.assertEqual(evaluate(rows)['status'], 'PASS')

    def test_transient_peak_can_settle_and_pass(self):
        rows=[sample(0), sample(140*1024, 50, 12), sample(80*1024, 20, 5), sample(20*1024, 4, 1), sample(10*1024, 2, 0), sample(8*1024, 1, 0)]
        result=evaluate(rows)
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['observed_peak']['rss_delta_kb'], 96*1024)

    def test_persistent_rss_growth_fails(self):
        rows=[sample(0), sample(10*1024), sample(30*1024), sample(100*1024), sample(110*1024), sample(120*1024)]
        result=evaluate(rows)
        self.assertEqual(result['status'], 'FAIL')
        self.assertTrue(any('RSS' in item for item in result['failures']))

    def test_persistent_fd_or_thread_growth_fails(self):
        rows=[sample(0), sample(), sample(), sample(0,40,9), sample(0,41,10), sample(0,42,11)]
        result=evaluate(rows)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(len(result['failures']), 2)

    def test_too_few_samples_is_insufficient(self):
        result=evaluate([sample(), sample()])
        self.assertEqual(result['status'], 'INSUFFICIENT')


if __name__ == '__main__':
    unittest.main()
