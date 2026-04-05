"""
Parallel file processing utilities for faster APK conversion.

Uses ThreadPoolExecutor for I/O-bound operations (file copying, image
resizing) which are the main bottleneck in large game APK conversions.
"""

from __future__ import annotations

import shutil
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Default worker count — tuned for I/O-bound file operations
DEFAULT_WORKERS = 8


def parallel_copy(
    file_pairs: list[tuple[Path, Path]],
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> tuple[int, int]:
    """
    Copy files in parallel using a thread pool.

    Args:
        file_pairs: list of (source, destination) Path tuples
        max_workers: thread count
        progress_callback: called with (completed, total) after each file

    Returns:
        (files_copied, bytes_copied)
    """
    if not file_pairs:
        return 0, 0

    total = len(file_pairs)
    completed = 0
    bytes_copied = 0

    def copy_one(src: Path, dst: Path) -> int:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return src.stat().st_size

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for src, dst in file_pairs:
            if src.is_file() and not dst.exists():
                futures[pool.submit(copy_one, src, dst)] = (src, dst)

        for future in as_completed(futures):
            try:
                size = future.result()
                bytes_copied += size
                completed += 1
                if progress_callback:
                    progress_callback(completed, total)
            except Exception as e:
                src, dst = futures[future]
                logger.warning("Failed to copy %s → %s: %s", src, dst, e)
                completed += 1

    return completed, bytes_copied


def parallel_process(
    items: list,
    worker_fn: Callable,
    max_workers: int = DEFAULT_WORKERS,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> list:
    """
    Process items in parallel. Returns list of results.

    Args:
        items: list of items to process
        worker_fn: function that takes one item and returns a result
        max_workers: thread count
        progress_callback: called with (completed, total)

    Returns:
        list of results (in completion order, not input order)
    """
    if not items:
        return []

    total = len(items)
    completed = 0
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker_fn, item): item for item in items}

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.warning("Processing failed for %s: %s", futures[future], e)
                results.append(None)

            completed += 1
            if progress_callback:
                progress_callback(completed, total)

    return results
