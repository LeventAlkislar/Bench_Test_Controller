"""Bounded retries for Windows file replacement conflicts."""
import os
import time

from bench_test.utils.debug_log import debug_log


REPLACE_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)


def replace_with_retry(source, destination):
    """Keep the same complete temporary file; return whether a retry was needed."""
    for attempt in range(len(REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return attempt > 0
        except OSError as exc:
            # Access denied and sharing/lock violations can be transient on Windows.
            retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33)
            if not retryable or attempt == len(REPLACE_RETRY_DELAYS):
                raise
            delay = REPLACE_RETRY_DELAYS[attempt]
            debug_log(
                f"File replacement blocked: {destination}; retry {attempt + 1}/"
                f"{len(REPLACE_RETRY_DELAYS)} in {delay:g}s: {exc}", level="warning")
            time.sleep(delay)
