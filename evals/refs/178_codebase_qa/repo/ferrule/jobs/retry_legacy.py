"""Pre-2.4 retry arithmetic, kept for ``scheduler.legacy_retry``.

The old scheduler counted attempts from zero and doubled unconditionally,
ignoring the per-family factor. It also allowed ``max_attempts`` *retries*
after the first attempt.
"""

from __future__ import annotations

from ferrule.jobs.retry import RetryPolicy


class LegacyRetryPolicy(RetryPolicy):
    def delay_ms(self, failures: int) -> int:
        attempt = max(failures - 1, 0)
        return int(min(self.base_ms * (2 ** attempt), self.cap_ms))

    def exhausted(self, attempts: int) -> bool:
        return attempts > self.max_attempts


def legacy_from(policy: RetryPolicy) -> LegacyRetryPolicy:
    return LegacyRetryPolicy(policy.base_ms, policy.factor, policy.cap_ms,
                             policy.max_attempts, policy.count_expiry)
