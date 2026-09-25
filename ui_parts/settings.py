# -*- coding: utf-8 -*-
"""Pure settings-screen copy/choice helpers."""

def backup_choices():
    return [
        ("Backup All Settings & Portals", "backup_all"),
        ("Restore Backup", "restore"),
    ]


def secret_backup_warning():
    # Kept as a compatibility import for older UI modules.
    return "Complete backups include all user-entered credentials and API keys."
