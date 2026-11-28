"""
AI resource protection (Phase P0B.1).

Reusable, dependency-free guards for expensive model calls:

- ConcurrencyLimiter: caps simultaneous inference runs and rejects
  extra callers immediately (no queueing).
- RateLimiter: in-memory sliding-window limit per client key.
- client_key(): derives a rate-limit key without blindly trusting
  client-supplied headers.

State is in memory and per process. Run the server as a single
uvicorn worker, otherwise each worker gets its own limits.

Agents do not import this module. Callers (routes/middleware)
wrap agent calls, so Agent 2 can reuse the same limiter instance
(same CPU / Ollama server) without changes to Agent 1.
"""

import ipaddress
import math
import threading
import time
from collections import deque
from contextlib import asynccontextmanager


# ------------------------------------------
# Concurrency
# ------------------------------------------

class LimiterBusy(Exception):
    """Raised when no inference slot is free."""


class ConcurrencyLimiter:
    """
    Non-blocking counting limiter.

    A threading lock (not asyncio) keeps it safe across event
    loops and threads; acquire/release never wait.
    """

    def __init__(self, max_concurrent: int = 1, name: str = "ai"):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1.")

        self.name = name
        self.max_concurrent = max_concurrent
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        return self._active

    @property
    def busy(self) -> bool:
        return self._active >= self.max_concurrent

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.max_concurrent:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("release() without acquire().")
            self._active -= 1

    @asynccontextmanager
    async def slot(self):
        """
        async with limiter.slot(): ...

        Raises LimiterBusy at once if full. The slot is released
        on success, on exceptions and on task cancellation.
        """
        if not self.try_acquire():
            raise LimiterBusy(self.name)

        try:
            yield
        finally:
            self.release()


# ------------------------------------------
# Rate limiting
# ------------------------------------------

class RateLimiter:
    """
    Sliding-window log: at most `limit` hits per `window_seconds`
    per key. Memory is bounded by max_keys * limit timestamps.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        max_keys: int = 10_000,
        clock=time.monotonic,
    ):
        if limit < 1 or window_seconds <= 0:
            raise ValueError("limit and window must be positive.")

        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """
        Record one request for `key` if allowed.

        Returns (allowed, retry_after_seconds). Rejected requests
        are not recorded, so retrying does not extend the block.
        """
        now = self._clock()
        cutoff = now - self.window_seconds

        with self._lock:
            hits = self._hits.get(key)

            if hits is None:
                self._make_room(cutoff)
                hits = self._hits[key] = deque()

            while hits and hits[0] <= cutoff:
                hits.popleft()

            if len(hits) >= self.limit:
                retry = hits[0] + self.window_seconds - now
                return False, max(1, math.ceil(retry))

            hits.append(now)
            return True, 0

    def _make_room(self, cutoff: float) -> None:

        if len(self._hits) < self.max_keys:
            return

        # Drop keys with no hits inside the window
        stale = [
            k for k, h in self._hits.items()
            if not h or h[-1] <= cutoff
        ]
        for k in stale:
            del self._hits[k]

        # Still full: evict the least recently active key
        while len(self._hits) >= self.max_keys:
            oldest = min(
                self._hits,
                key=lambda k: self._hits[k][-1] if self._hits[k] else 0
            )
            del self._hits[oldest]


# ------------------------------------------
# Client identity
# ------------------------------------------

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Set by Cloudflare at its edge; a visitor cannot choose it
# when the request really came through the tunnel.
CLOUDFLARE_CLIENT_HEADER = "cf-connecting-ip"


def _normalise_ip(raw: str) -> str | None:
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None

    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        # One IPv6 user usually controls a whole /64
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))

    return str(ip)


def tunnel_client_ip(
    client_host: str | None,
    headers: dict[str, str]
) -> str | None:
    """
    Normalised visitor IP if the request genuinely came through
    the local Cloudflare Tunnel, otherwise None.

    Requires: TCP peer is loopback (cloudflared runs locally) AND
    a valid CF-Connecting-IP AND a CF-Ray header. A remote peer
    sending these headers is never treated as tunnel traffic,
    and X-Forwarded-For / Forwarded are ignored.
    """
    if client_host not in LOOPBACK_HOSTS:
        return None

    if not headers.get("cf-ray"):
        return None

    raw = headers.get(CLOUDFLARE_CLIENT_HEADER)

    if raw is None:
        return None

    return _normalise_ip(raw)


def client_key(client_host: str | None, headers: dict[str, str]) -> str:
    """
    Rate-limit key for a request.

    - CF-Connecting-IP is trusted ONLY when the TCP peer is
      loopback (i.e. the request came from local cloudflared).
      A remote peer sending it is keyed by its real address.
    - X-Forwarded-For / Forwarded are never trusted.
    - Unparseable values share one bucket (fail closed).
    """
    peer = client_host or "unknown"

    if peer in LOOPBACK_HOSTS:
        forwarded = headers.get(CLOUDFLARE_CLIENT_HEADER)

        if forwarded is not None:
            ip = _normalise_ip(forwarded)
            return f"cf:{ip}" if ip else "cf:invalid"

        return "local"

    ip = _normalise_ip(peer)
    return f"ip:{ip}" if ip else "peer:unknown"
