# -*- coding: utf-8 -*-
"""Typed plugin errors used across protocol, storage and playback layers."""

class PluginError(Exception):
    code = "PLUGIN_ERROR"

    def __init__(self, message="", details=None):
        super().__init__(str(message or self.code))
        self.details = details


class PortalConnectionError(PluginError):
    code = "PORTAL_CONNECTION"


class PortalAuthenticationError(PluginError):
    code = "PORTAL_AUTH"


class PortalRateLimitError(PluginError):
    code = "PORTAL_RATE_LIMIT"


class PortalResponseError(PluginError):
    code = "PORTAL_RESPONSE"


class PlaybackLinkError(PluginError):
    code = "PLAYBACK_LINK"


class StorageError(PluginError):
    code = "STORAGE"


class CancelledError(PluginError):
    code = "CANCELLED"
