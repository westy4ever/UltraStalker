# -*- coding: utf-8 -*-
from __future__ import absolute_import

import threading
import unittest

from ..core import bouquets


class _FakeThread(object):
    def __init__(self, alive=True, stop_on_join=False, name="fake-epg"):
        self.alive = bool(alive)
        self.stop_on_join = bool(stop_on_join)
        self.name = name
        self.joins = 0

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins += 1
        if self.stop_on_join:
            self.alive = False


class _FakeClient(object):
    def __init__(self):
        self.cancels = 0

    def cancel_pending_requests(self):
        self.cancels += 1


class EPGWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.old_orphans = bouquets._XMLTV_ORPHANS
        self.old_active = bouquets._XMLTV_ACTIVE
        self.old_refreshing = bouquets._XMLTV_REFRESHING
        self.old_paused = bouquets._XMLTV_PAUSED.is_set()
        bouquets._XMLTV_ORPHANS = {}
        bouquets._XMLTV_ACTIVE = {}
        bouquets._XMLTV_REFRESHING = set()
        bouquets._XMLTV_PAUSED.clear()

    def tearDown(self):
        bouquets._XMLTV_ORPHANS = self.old_orphans
        bouquets._XMLTV_ACTIVE = self.old_active
        bouquets._XMLTV_REFRESHING = self.old_refreshing
        if self.old_paused:
            bouquets._XMLTV_PAUSED.set()
        else:
            bouquets._XMLTV_PAUSED.clear()

    @staticmethod
    def _state(thread, client=None):
        client = client or _FakeClient()
        return {
            "threads": [thread],
            "cancel": threading.Event(),
            "clients": {client},
            "clients_lock": threading.RLock(),
            "since": 1.0,
        }, client

    def test_reaper_drops_finished_orphans(self):
        dead_state, _ = self._state(_FakeThread(alive=False))
        live = _FakeThread(alive=True)
        live_state, _ = self._state(live)
        bouquets._XMLTV_ORPHANS.update({"dead": dead_state, "live": live_state})
        with bouquets._XMLTV_REFRESH_LOCK:
            total = bouquets._reap_xmltv_orphans_locked()
        self.assertEqual(total, 1)
        self.assertNotIn("dead", bouquets._XMLTV_ORPHANS)
        self.assertEqual(bouquets._XMLTV_ORPHANS["live"]["threads"], [live])

    def test_pause_recancels_orphan_clients_and_reports_live_thread(self):
        thread = _FakeThread(alive=True, stop_on_join=False)
        state, client = self._state(thread)
        bouquets._XMLTV_ORPHANS["portal"] = state
        self.assertFalse(bouquets.pause_xmltv_refreshes(wait=True, timeout=0.01))
        self.assertTrue(state["cancel"].is_set())
        self.assertEqual(client.cancels, 1)
        self.assertGreaterEqual(thread.joins, 1)

    def test_pause_reaps_orphan_that_stops_during_join(self):
        thread = _FakeThread(alive=True, stop_on_join=True)
        state, client = self._state(thread)
        bouquets._XMLTV_ORPHANS["portal"] = state
        self.assertTrue(bouquets.pause_xmltv_refreshes(wait=True, timeout=0.1))
        self.assertEqual(client.cancels, 1)
        self.assertNotIn("portal", bouquets._XMLTV_ORPHANS)

    def test_global_orphan_cap_suppresses_new_refresh_generation(self):
        for index in range(bouquets._XMLTV_ORPHAN_MAX_THREADS):
            state, _ = self._state(_FakeThread(alive=True, name="orphan-%d" % index))
            bouquets._XMLTV_ORPHANS["p%d" % index] = state
        self.assertFalse(bouquets.refresh_xmltv_async("new-profile", force=True))
        self.assertNotIn("new-profile", bouquets._XMLTV_ACTIVE)
        self.assertNotIn("new-profile", bouquets._XMLTV_REFRESHING)


if __name__ == '__main__':
    unittest.main()
