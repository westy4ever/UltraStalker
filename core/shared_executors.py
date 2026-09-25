# -*- coding: utf-8 -*-
"""Shared bounded worker lanes for Ultra Stalker UI background work.

Performance step 3: keep the existing jobs and callbacks intact, but cap the
number of simultaneously-created worker threads across screens.  The pools are
lazy, so importing this module does not start any threads.

The lanes are deliberately split by work type so a slow provider/catalogue
request cannot monopolise the same workers used for visible artwork.
"""
from .executor import LazyThreadPoolExecutor

# Visible artwork can involve provider/TMDB HTTP plus file decode/write.  Four
# workers is the old single-pool ceiling already used by provider first-paint,
# now shared by Grid + Search instead of each screen owning more workers.
VISIBLE_ARTWORK_EXECUTOR = LazyThreadPoolExecutor(
    max_workers=4, thread_name_prefix="ultrastalker-artwork"
)

# R231: provider catalogue posters are the low-latency first-paint hedge for
# visible VOD/Series cards. Keep these HTTP reads independent from the canonical
# TMDb/ArtworkV2 lane above: a slow provider CDN must not block TMDb fallback,
# and a slow TMDb match must not leave provider-backed cards as placeholders.
VISIBLE_PROVIDER_ARTWORK_EXECUTOR = LazyThreadPoolExecutor(
    max_workers=4, thread_name_prefix="ultrastalker-provider-poster"
)

# Catalogue/page warming is intentionally small.  Two workers preserve the old
# ability for a hierarchy/category request and a page warm to overlap, while
# preventing several screens from each creating their own one-worker pool.
CATALOGUE_EXECUTOR = LazyThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ultrastalker-catalogue"
)

# Small metadata/EPG/TMDB lookups.  Three workers preserve the two-wide visible
# TMDB lane and leave one slot for EPG/cast metadata without allowing every
# screen to spawn its own metadata workers.
METADATA_EXECUTOR = LazyThreadPoolExecutor(
    max_workers=3, thread_name_prefix="ultrastalker-metadata"
)

# Read-mostly local cache jobs (poster HDD batches / picon cache).  Keep these
# off the image-generation lane and cap them globally.
CACHE_IO_EXECUTOR = LazyThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ultrastalker-cacheio"
)

_SHARED = (
    VISIBLE_ARTWORK_EXECUTOR,
    VISIBLE_PROVIDER_ARTWORK_EXECUTOR,
    CATALOGUE_EXECUTOR,
    METADATA_EXECUTOR,
    CACHE_IO_EXECUTOR,
)


def shutdown_shared_executors(wait=False):
    """Stop all shared lanes once during plugin/runtime shutdown."""
    for executor in _SHARED:
        try:
            executor.shutdown(wait=bool(wait), cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=bool(wait))
