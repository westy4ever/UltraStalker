# -*- coding: utf-8 -*-
"""Pure settings-screen copy/choice helpers."""

def backup_choices():
    return [
        ("Create backup (portable, no API secrets)","backup_safe"),
        ("Create authenticated backup (this receiver)","backup_auth"),
        ("Create backup including api_keys.conf","backup_secrets"),
        ("Create authenticated backup + api_keys.conf","backup_auth_secrets"),
        ("Restore a backup","restore"),
    ]


def secret_backup_warning():
    return "This backup will contain api_keys.conf credentials. The ZIP is protected by filesystem permissions but is NOT encrypted. Create it only on trusted storage."
