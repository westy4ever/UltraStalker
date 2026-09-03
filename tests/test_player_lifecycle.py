# -*- coding: utf-8 -*-
import queue
import threading
import unittest

from ..services.player_lifecycle_guard import begin_async_cleanup


class _DummyPlayer(object):
    def __init__(self):
        self._lifecycle_cleanup_done = False
        self._closing_playback = False
        self._online_subtitle_generation = 7
        self._zap_switch_generation = 11
        self._online_subtitle_searching = True
        self._zap_switch_inflight = True
        self._zap_switch_started_at = 123.5
        self._recovery_inflight = True
        self._online_subtitle_cancel = threading.Event()
        self._recovery_cancel = threading.Event()
        self._online_subtitle_queue = queue.Queue()
        self._recovery_queue = queue.Queue()
        self._zap_switch_queue = queue.Queue()


class PlayerLifecycleTests(unittest.TestCase):
    def test_cleanup_invalidates_async_workers_before_close(self):
        player = _DummyPlayer()
        player._online_subtitle_queue.put(("subtitle", object()))
        player._recovery_queue.put(("recovery", object()))
        player._zap_switch_queue.put(("zap", object()))

        self.assertTrue(begin_async_cleanup(player))
        self.assertTrue(player._closing_playback)
        self.assertEqual(player._online_subtitle_generation, 8)
        self.assertEqual(player._zap_switch_generation, 12)
        self.assertFalse(player._online_subtitle_searching)
        self.assertFalse(player._zap_switch_inflight)
        self.assertEqual(player._zap_switch_started_at, 0.0)
        self.assertFalse(player._recovery_inflight)
        self.assertTrue(player._online_subtitle_cancel.is_set())
        self.assertTrue(player._recovery_cancel.is_set())
        self.assertTrue(player._online_subtitle_queue.empty())
        self.assertTrue(player._recovery_queue.empty())
        self.assertTrue(player._zap_switch_queue.empty())

    def test_cleanup_is_idempotent_and_does_not_advance_generations_twice(self):
        player = _DummyPlayer()
        self.assertTrue(begin_async_cleanup(player))
        subtitle_generation = player._online_subtitle_generation
        zap_generation = player._zap_switch_generation

        self.assertFalse(begin_async_cleanup(player))
        self.assertEqual(player._online_subtitle_generation, subtitle_generation)
        self.assertEqual(player._zap_switch_generation, zap_generation)

    def test_late_worker_result_is_stale_after_cleanup(self):
        player = _DummyPlayer()
        worker_generation = player._zap_switch_generation
        begin_async_cleanup(player)

        # A blocked worker can finish after Screen.close(), but it cannot match
        # the active generation any more and therefore must be ignored by UI code.
        player._zap_switch_queue.put((worker_generation, 1, {}, "http://example/stream", None))
        result_generation = player._zap_switch_queue.get_nowait()[0]
        self.assertNotEqual(result_generation, player._zap_switch_generation)

    def test_cleanup_tolerates_partially_initialized_player(self):
        class Partial(object):
            pass
        player = Partial()
        self.assertTrue(begin_async_cleanup(player))
        self.assertTrue(player._closing_playback)
        self.assertEqual(player._online_subtitle_generation, 1)
        self.assertEqual(player._zap_switch_generation, 1)


if __name__ == '__main__':
    unittest.main()
