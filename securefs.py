# -*- coding: utf-8 -*-
"""Small filesystem hardening helpers for private runtime directories."""
from __future__ import absolute_import
import os
import stat


def secure_private_dir(path, mode=0o700):
    """Create/validate a private directory without following a pre-existing symlink."""
    path = os.path.abspath(str(path or ''))
    if not path or path == os.sep:
        raise OSError('Refusing unsafe private directory path')
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        os.makedirs(path, mode=mode, exist_ok=False)
        st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise OSError('Private directory must not be a symlink: %s' % path)
    if not stat.S_ISDIR(st.st_mode):
        raise OSError('Private path is not a directory: %s' % path)
    # For private /tmp caches, reject directories owned by another local uid.
    if path.startswith('/tmp/') and hasattr(os, 'geteuid') and st.st_uid != os.geteuid():
        raise OSError('Private temporary directory has unexpected owner: %s' % path)
    os.chmod(path, mode)
    return path
