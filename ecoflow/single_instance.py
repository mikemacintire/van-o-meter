"""Cross-process single-instance lock via an exclusively-bound localhost port.

2026-08-05: three concurrently-launched dashboards each ran a balancer thread,
and the one holding a stale stop threshold killed a B->A transfer at 57%. A
bound port is the simplest lock the OS cleans up on process death — no stale
lockfiles after a crash.

Windows note: plain bind() here is stealable (SO_REUSEADDR semantics let a
later process take the port), so SO_EXCLUSIVEADDRUSE is required for the bind
to actually act as a mutex. POSIX bind() is already exclusive without it.
"""

import socket


def acquire(port):
    """Return a held socket (keep a reference for process lifetime), or None
    if another process already holds `port`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    flag = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if flag is not None:
        s.setsockopt(socket.SOL_SOCKET, flag, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        return None
    return s
