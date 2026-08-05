"""Tests for the cross-process single-instance lock.

2026-08-05: three concurrently-launched dashboards each ran a balancer thread;
the one with a stale stop threshold killed a B->A transfer at 57%. The lock is
a bound localhost port — the OS releases it on process death, so a crashed
holder can never wedge the next launch.
"""

from ecoflow import single_instance

PORT = 38642   # test-only port, nothing binds it outside this file


def test_acquire_returns_held_lock():
    lock = single_instance.acquire(PORT)
    try:
        assert lock is not None
    finally:
        lock.close()


def test_second_acquire_refused_while_held():
    lock = single_instance.acquire(PORT)
    try:
        assert single_instance.acquire(PORT) is None
    finally:
        lock.close()


def test_release_allows_reacquire():
    single_instance.acquire(PORT).close()
    lock = single_instance.acquire(PORT)
    try:
        assert lock is not None
    finally:
        lock.close()
