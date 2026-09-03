# -*- coding: utf-8 -*-
"""Evaluate UltraStalker receiver soak samples from runtime.log.

Usage on receiver:
    python3 /usr/lib/enigma2/python/Plugins/Extensions/UltraStalker/run_receiver_soak_check.py
    python3 .../run_receiver_soak_check.py /tmp/ultrastalker/runtime.log

Exit 0 = pass, 1 = resource creep/failure, 2 = insufficient/invalid samples.
Only privacy-safe soak_resource_sample rows are consumed.
"""
from __future__ import print_function

import json
import os
import sys

DEFAULT_LOG = "/tmp/ultrastalker/runtime.log"
MIN_SAMPLES = 5
SETTLE_SAMPLES = 3
RSS_LIMIT_KB = 96 * 1024
FD_LIMIT = 32
THREAD_LIMIT = 8


def _int(row, key):
    try:
        return int(row.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def load_samples(path):
    samples = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(row, dict) and row.get("event") == "soak_resource_sample":
                samples.append(row)
    return samples


def evaluate(samples, min_samples=MIN_SAMPLES, settle_samples=SETTLE_SAMPLES):
    rows = list(samples or [])
    if len(rows) < int(min_samples):
        return {"status": "INSUFFICIENT", "samples": len(rows), "failures": ["need at least %d soak samples" % int(min_samples)]}

    count = max(1, min(int(settle_samples), len(rows)))
    settled = rows[-count:]
    failures = []
    settled_max = {
        "rss_delta_kb": max(_int(row, "rss_delta_kb") for row in settled),
        "fd_delta": max(_int(row, "fd_delta") for row in settled),
        "thread_delta": max(_int(row, "thread_delta") for row in settled),
    }
    if settled_max["rss_delta_kb"] > RSS_LIMIT_KB:
        failures.append("settled RSS growth exceeds +96 MiB")
    if settled_max["fd_delta"] > FD_LIMIT:
        failures.append("settled FD growth exceeds +32")
    if settled_max["thread_delta"] > THREAD_LIMIT:
        failures.append("settled thread growth exceeds +8")

    # Keep transient peaks visible for investigation without failing a run that
    # demonstrably returned below the guard during the final settle window.
    observed_peak = {
        "rss_delta_kb": max(_int(row, "rss_delta_kb") for row in rows),
        "fd_delta": max(_int(row, "fd_delta") for row in rows),
        "thread_delta": max(_int(row, "thread_delta") for row in rows),
        "rss_peak_kb": max(_int(row, "rss_peak_kb") for row in rows),
    }
    return {
        "status": "FAIL" if failures else "PASS",
        "samples": len(rows),
        "settle_samples": count,
        "settled_max": settled_max,
        "observed_peak": observed_peak,
        "failures": failures,
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    path = args[0] if args else DEFAULT_LOG
    if not os.path.isfile(path):
        print("SOAK CHECK: INSUFFICIENT")
        print(" - runtime log not found: %s" % path)
        return 2
    try:
        result = evaluate(load_samples(path))
    except (OSError, IOError) as exc:
        print("SOAK CHECK: INSUFFICIENT")
        print(" - cannot read runtime log: %s" % exc)
        return 2

    print("SOAK CHECK: %s" % result["status"])
    print(" samples=%d settle_samples=%s" % (result.get("samples", 0), result.get("settle_samples", 0)))
    if result.get("settled_max"):
        value = result["settled_max"]
        print(" settled: rss=%+.1f MiB fds=%+d threads=%+d" % (
            value["rss_delta_kb"] / 1024.0, value["fd_delta"], value["thread_delta"]))
    if result.get("observed_peak"):
        value = result["observed_peak"]
        print(" observed max delta: rss=%+.1f MiB fds=%+d threads=%+d" % (
            value["rss_delta_kb"] / 1024.0, value["fd_delta"], value["thread_delta"]))
    for failure in result.get("failures") or ():
        print(" - %s" % failure)
    if result["status"] == "PASS":
        return 0
    if result["status"] == "INSUFFICIENT":
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
