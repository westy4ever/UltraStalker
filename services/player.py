# -*- coding: utf-8 -*-
"""Ultra Stalker player public boundary.

The receiver-proven beta57 playback implementation is intentionally isolated
behind this stable import surface. New catalogue/UI code must not alter the
playback lifecycle accidentally.
"""
from __future__ import absolute_import

from .player_native import UltraStalkerPlayer, force_session_silence, shutdown_player_workers

__all__ = ("UltraStalkerPlayer", "force_session_silence", "shutdown_player_workers")
