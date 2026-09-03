# -*- coding: utf-8 -*-
"""Safe compatibility calls without exception-driven API probing.

A callable's signature is inspected *before* execution.  This keeps support for
older provider/framework call shapes while ensuring a TypeError raised inside
the callable is never mistaken for a signature mismatch and retried.
"""
from __future__ import absolute_import

import inspect


def call_compatible(func, candidates):
    """Call *func* once using the first signature-compatible candidate.

    ``candidates`` is an iterable of ``(args, kwargs)`` pairs ordered from the
    preferred/newest call shape to legacy shapes.  If introspection is not
    available for a callable, the preferred shape is executed exactly once;
    runtime ``TypeError`` is deliberately allowed to propagate.
    """
    choices = list(candidates or ())
    if not choices:
        raise TypeError("no call candidates supplied")

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        args, kwargs = choices[0]
        return func(*tuple(args or ()), **dict(kwargs or {}))

    for args, kwargs in choices:
        args = tuple(args or ())
        kwargs = dict(kwargs or {})
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return func(*args, **kwargs)

    raise TypeError("no compatible call signature for %r" % (func,))
