"""Phase P0B.1: AI concurrency + rate limiting (Ollama mocked)."""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import SecurityConfigError, SecuritySettings
from app.main import create_app
from app.routes import intake as intake_route
from app.schemas.intake import IntakeResult
from app.services.ai_limits import (
    ConcurrencyLimiter,
    LimiterBusy,
    RateLimiter,
    client_key,
)


ADMIN_KEY = "test-admin-key-not-real-0123456789abcdef"
AI_FORM = {
    "agreement_type": "house_rent",
    "agreement_text": "1. Rent\nThe tenant pays rent monthly.",
}


def make_client(client_host="127.0.0.1", **overrides) -> TestClient:
    values = dict(admin_enabled=True, admin_key=ADMIN_KEY)
    values.update(overrides)
    return TestClient(
        create_app(SecuritySettings(**values)),
        base_url="https://testserver",
        client=(client_host, 50000),
        follow_redirects=False,
    )


def tunnel(ip):
    return {"CF-Connecting-IP": ip, "CF-Ray": "abc"}


@pytest.fixture
def intake_spy(monkeypatch):
    calls = []

    async def fake_intake(text, agreement_type="unknown"):
        calls.append(text)
        return IntakeResult(agreement_type="house_rent", language="en")

    monkeypatch.setattr(intake_route, "run_intake_agent", fake_intake)
    return calls


# ------------------------------------------
# ConcurrencyLimiter
# ------------------------------------------

def test_limiter_rejects_when_full_and_frees_slot():
    limiter = ConcurrencyLimiter(1)

    assert limiter.try_acquire() is True
    assert limiter.busy and limiter.try_acquire() is False

    limiter.release()
    assert limiter.active == 0 and limiter.try_acquire() is True


def test_limiter_release_without_acquire_raises():
    with pytest.raises(RuntimeError):
        ConcurrencyLimiter(1).release()


def test_limiter_slot_busy_raises_immediately():
    limiter = ConcurrencyLimiter(1)
    limiter.try_acquire()

    async def run():
        async with limiter.slot():
            pass

    start = time.monotonic()
    with pytest.raises(LimiterBusy):
        asyncio.run(run())
    assert time.monotonic() - start < 0.5


def test_limiter_released_after_exception():
    limiter = ConcurrencyLimiter(1)

    async def run():
        async with limiter.slot():
            raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        asyncio.run(run())
    assert limiter.active == 0


def test_limiter_released_after_cancellation():
    limiter = ConcurrencyLimiter(1)

    async def run():
        async def work():
            async with limiter.slot():
                await asyncio.sleep(10)

        task = asyncio.create_task(work())
        await asyncio.sleep(0.01)
        assert limiter.active == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert limiter.active == 0


def test_limiter_requires_positive_size():
    with pytest.raises(ValueError):
        ConcurrencyLimiter(0)


# ------------------------------------------
# RateLimiter
# ------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_rate_limit_blocks_then_recovers():
    clock = FakeClock()
    limiter = RateLimiter(2, 60, clock=clock)

    assert limiter.hit("a") == (True, 0)
    clock.now += 10
    assert limiter.hit("a") == (True, 0)

    allowed, retry = limiter.hit("a")
    assert allowed is False and retry == 50

    # Other clients are unaffected
    assert limiter.hit("b")[0] is True

    clock.now += 50
    assert limiter.hit("a")[0] is True


def test_rejected_hits_do_not_extend_block():
    clock = FakeClock()
    limiter = RateLimiter(1, 60, clock=clock)
    limiter.hit("a")

    for _ in range(20):
        clock.now += 1
        assert limiter.hit("a")[0] is False

    clock.now += 40
    assert limiter.hit("a")[0] is True


def test_rate_limiter_memory_is_bounded():
    limiter = RateLimiter(1, 60, max_keys=50, clock=FakeClock())

    for i in range(500):
        limiter.hit(f"client-{i}")

    assert len(limiter._hits) <= 50


# ------------------------------------------
# Client identity (header trust)
# ------------------------------------------

def test_cloudflare_ip_trusted_only_from_loopback_peer():
    assert client_key("127.0.0.1", {"cf-connecting-ip": "198.51.100.7"}) \
        == "cf:198.51.100.7"
    # A remote peer cannot choose its key with the header
    assert client_key("203.0.113.9", {"cf-connecting-ip": "1.2.3.4"}) \
        == "ip:203.0.113.9"


def test_forwarded_for_is_never_trusted():
    assert client_key("127.0.0.1", {"x-forwarded-for": "9.9.9.9"}) == "local"
    assert client_key("203.0.113.9", {"x-forwarded-for": "9.9.9.9"}) \
        == "ip:203.0.113.9"


def test_invalid_cloudflare_ip_shares_one_bucket():
    assert client_key("127.0.0.1", {"cf-connecting-ip": "not-an-ip"}) \
        == "cf:invalid"
    assert client_key("127.0.0.1", {"cf-connecting-ip": "x, y"}) \
        == "cf:invalid"


def test_ipv6_clients_grouped_by_64():
    a = client_key("::1", {"cf-connecting-ip": "2001:db8:1:2::1"})
    b = client_key("::1", {"cf-connecting-ip": "2001:db8:1:2:ffff::9"})
    assert a == b == "cf:2001:db8:1:2::/64"


# ------------------------------------------
# Settings
# ------------------------------------------

def test_default_ai_limits_are_conservative():
    settings = SecuritySettings.from_env({})
    assert settings.ai_max_concurrent == 1
    assert settings.ai_rate_limit == 3
    assert settings.ai_rate_window_seconds == 600


def test_ai_limits_configurable():
    settings = SecuritySettings.from_env({
        "TOR_LAW_AI_MAX_CONCURRENT": "2",
        "TOR_LAW_AI_RATE_LIMIT": "10",
        "TOR_LAW_AI_RATE_WINDOW_SECONDS": "60",
    })
    assert (settings.ai_max_concurrent, settings.ai_rate_limit,
            settings.ai_rate_window_seconds) == (2, 10, 60)


@pytest.mark.parametrize("environ", [
    {"TOR_LAW_AI_MAX_CONCURRENT": "0"},
    {"TOR_LAW_AI_MAX_CONCURRENT": "50"},
    {"TOR_LAW_AI_RATE_LIMIT": "0"},
    {"TOR_LAW_AI_RATE_LIMIT": "lots"},
    {"TOR_LAW_AI_RATE_WINDOW_SECONDS": "1"},
])
def test_invalid_ai_limits_fail_closed(environ):
    with pytest.raises(SecurityConfigError):
        SecuritySettings.from_env(environ)


# ------------------------------------------
# /intake/analyze integration
# ------------------------------------------

def test_rate_limit_returns_429_before_agent(intake_spy):
    client = make_client(ai_rate_limit=2)

    assert client.post("/intake/analyze", data=AI_FORM).status_code == 200
    assert client.post("/intake/analyze", data=AI_FORM).status_code == 200

    response = client.post("/intake/analyze", data=AI_FORM)
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert "Too many AI analysis requests" in response.text
    assert len(intake_spy) == 2


def test_tunnel_clients_have_separate_buckets(intake_spy):
    app_client = make_client(ai_rate_limit=1)
    login = app_client.post("/admin/login", data={"admin_key": ADMIN_KEY})
    assert login.status_code == 303

    first = app_client.post("/intake/analyze", data=AI_FORM,
                            headers=tunnel("198.51.100.1"))
    again = app_client.post("/intake/analyze", data=AI_FORM,
                            headers=tunnel("198.51.100.1"))
    other = app_client.post("/intake/analyze", data=AI_FORM,
                            headers=tunnel("198.51.100.2"))

    assert (first.status_code, again.status_code, other.status_code) \
        == (200, 429, 200)


def test_spoofed_forwarded_header_does_not_bypass_limit(intake_spy):
    client = make_client(client_host="127.0.0.1", ai_rate_limit=1)

    assert client.post("/intake/analyze", data=AI_FORM).status_code == 200

    response = client.post("/intake/analyze", data=AI_FORM,
                           headers={"X-Forwarded-For": "10.0.0.99"})
    # Proxy header makes it non-local (access check), and even if it
    # were allowed it would not get a fresh bucket.
    assert response.status_code in (429, 503)
    assert len(intake_spy) == 1


def test_access_check_runs_before_rate_limit(intake_spy):
    client = make_client(client_host="203.0.113.9", ai_rate_limit=1)

    for _ in range(3):
        response = client.post("/intake/analyze", data=AI_FORM)
        assert response.status_code == 503
        assert "temporarily unavailable" in response.text

    # Rejected-for-access requests never touched the rate limiter
    assert client.app.state.ai_rate_limiter._hits == {}
    assert intake_spy == []


def test_busy_rejected_early_without_using_rate_budget(intake_spy):
    client = make_client(ai_rate_limit=1)
    limiter = client.app.state.ai_inference_limiter
    limiter.try_acquire()  # simulate a running inference

    response = client.post("/intake/analyze", data=AI_FORM)
    assert response.status_code == 503
    assert "busy" in response.text
    assert response.headers["retry-after"] == "30"
    assert intake_spy == []

    limiter.release()
    assert client.post("/intake/analyze", data=AI_FORM).status_code == 200


def test_route_rechecks_slot_authoritatively(intake_spy):
    # Middleware precheck passes, but the slot is taken by the time
    # the route acquires (race): the route must still refuse.
    client = make_client()
    client.app.state.ai_inference_limiter = ConcurrencyLimiter(1)
    client.app.state.ai_inference_limiter.try_acquire()

    response = client.post("/intake/analyze", data=AI_FORM)
    assert response.status_code == 503
    assert "busy" in response.text
    assert intake_spy == []


def test_second_simultaneous_request_rejected(monkeypatch):
    started = threading.Event()
    finish = threading.Event()

    async def slow_intake(text, agreement_type="unknown"):
        started.set()
        while not finish.is_set():
            await asyncio.sleep(0.01)
        return IntakeResult(agreement_type="house_rent", language="en")

    monkeypatch.setattr(intake_route, "run_intake_agent", slow_intake)
    client = make_client()
    results = {}

    def first():
        results["first"] = client.post("/intake/analyze", data=AI_FORM)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert started.wait(5)
        second = client.post("/intake/analyze", data=AI_FORM)
        assert second.status_code == 503
        assert "busy" in second.text
    finally:
        finish.set()
        thread.join(5)

    assert results["first"].status_code == 200
    assert client.app.state.ai_inference_limiter.active == 0


def test_slot_released_after_agent_failure(monkeypatch):
    async def broken(text, agreement_type="unknown"):
        raise ConnectionError("ollama down")

    monkeypatch.setattr(intake_route, "run_intake_agent", broken)
    client = make_client()

    for _ in range(2):
        response = client.post("/intake/analyze", data=AI_FORM)
        assert response.status_code == 503
        assert "AI analysis failed" in response.text

    assert client.app.state.ai_inference_limiter.active == 0


def test_validation_errors_do_not_hold_slot(intake_spy):
    client = make_client()
    response = client.post(
        "/intake/analyze",
        data={"agreement_type": "house_rent", "agreement_text": "x" * 6001},
    )
    assert response.status_code == 400
    assert client.app.state.ai_inference_limiter.active == 0


def test_public_pages_not_rate_limited():
    client = make_client(client_host="203.0.113.9", ai_rate_limit=1)
    for _ in range(5):
        assert client.get("/review").status_code == 200
        assert client.post(
            "/reviews/preview",
            data={"agreement_type": "house_rent", "agreement_text": "Hi"},
        ).status_code == 200


# ------------------------------------------
# P0B.1a: temporary team testing through Cloudflare Tunnel
# ------------------------------------------

from app.services.ai_limits import tunnel_client_ip  # noqa: E402


def test_tunnel_ai_flag_defaults_off():
    assert SecuritySettings.from_env({}).allow_tunnel_ai is False
    assert SecuritySettings.from_env(
        {"TOR_LAW_ALLOW_TUNNEL_AI": "1"}
    ).allow_tunnel_ai is True
    with pytest.raises(SecurityConfigError):
        SecuritySettings.from_env({"TOR_LAW_ALLOW_TUNNEL_AI": "sure"})


def test_tunnel_identity_requires_loopback_peer_and_cf_headers():
    good = {"cf-connecting-ip": "198.51.100.7", "cf-ray": "abc"}

    assert tunnel_client_ip("127.0.0.1", good) == "198.51.100.7"
    assert tunnel_client_ip("::1", good) == "198.51.100.7"
    # Remote peer faking Cloudflare headers
    assert tunnel_client_ip("203.0.113.9", good) is None
    # Missing CF-Ray / missing or invalid client IP
    assert tunnel_client_ip("127.0.0.1", {"cf-connecting-ip": "1.2.3.4"}) is None
    assert tunnel_client_ip("127.0.0.1", {"cf-ray": "abc"}) is None
    assert tunnel_client_ip(
        "127.0.0.1", {"cf-connecting-ip": "evil", "cf-ray": "abc"}
    ) is None
    # X-Forwarded-For is never enough
    assert tunnel_client_ip("127.0.0.1", {"x-forwarded-for": "1.2.3.4"}) is None


def test_tunnel_ai_blocked_by_default(intake_spy):
    client = make_client(admin_enabled=False)

    response = client.post("/intake/analyze", data=AI_FORM,
                           headers=tunnel("198.51.100.1"))
    assert response.status_code == 503
    assert "temporarily unavailable" in response.text
    assert intake_spy == []


def test_tunnel_ai_allowed_when_flag_on(intake_spy):
    client = make_client(admin_enabled=False, allow_tunnel_ai=True)

    response = client.post("/intake/analyze", data=AI_FORM,
                           headers=tunnel("198.51.100.1"))
    assert response.status_code == 200
    assert len(intake_spy) == 1


@pytest.mark.parametrize("peer, headers", [
    # Remote peer (server bound beyond loopback) faking Cloudflare
    ("203.0.113.9", {"CF-Connecting-IP": "198.51.100.1", "CF-Ray": "x"}),
    # Public visitor without tunnel headers
    ("203.0.113.9", {}),
    # Local proxy that is not cloudflared
    ("127.0.0.1", {"X-Forwarded-For": "198.51.100.1"}),
    # Incomplete / invalid Cloudflare headers
    ("127.0.0.1", {"CF-Connecting-IP": "198.51.100.1"}),
    ("127.0.0.1", {"CF-Connecting-IP": "not-an-ip", "CF-Ray": "x"}),
])
def test_flag_does_not_open_ai_to_non_tunnel_requests(
    intake_spy, peer, headers
):
    client = make_client(client_host=peer, admin_enabled=False,
                         allow_tunnel_ai=True)

    response = client.post("/intake/analyze", data=AI_FORM, headers=headers)
    assert response.status_code == 503
    assert intake_spy == []


def test_tunnel_ai_keeps_per_client_rate_limit(intake_spy):
    client = make_client(admin_enabled=False, allow_tunnel_ai=True,
                         ai_rate_limit=2)

    codes = [
        client.post("/intake/analyze", data=AI_FORM,
                    headers=tunnel("198.51.100.1")).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]

    # A different tester has their own budget; spoofed XFF is ignored
    other = client.post(
        "/intake/analyze", data=AI_FORM,
        headers={**tunnel("198.51.100.2"), "X-Forwarded-For": "198.51.100.1"},
    )
    assert other.status_code == 200

    blocked = client.post("/intake/analyze", data=AI_FORM,
                          headers=tunnel("198.51.100.1"))
    assert "Too many AI analysis requests" in blocked.text
    assert len(intake_spy) == 3


def test_tunnel_ai_keeps_shared_concurrency_limit(intake_spy):
    client = make_client(admin_enabled=False, allow_tunnel_ai=True)
    client.app.state.ai_inference_limiter.try_acquire()

    response = client.post("/intake/analyze", data=AI_FORM,
                           headers=tunnel("198.51.100.1"))
    assert response.status_code == 503
    assert "busy with another agreement" in response.text
    assert intake_spy == []


def test_tunnel_flag_does_not_grant_admin_access():
    client = make_client(allow_tunnel_ai=True)

    assert client.get("/admin/legal-sources/",
                      headers=tunnel("198.51.100.1")).status_code == 403
    assert client.get("/admin/laws",
                      headers=tunnel("198.51.100.1")).status_code == 303


def test_tunnel_flag_keeps_site_public_and_docs_off():
    client = make_client(client_host="127.0.0.1", allow_tunnel_ai=True)
    for path in ("/", "/review", "/static/css/style.css"):
        response = client.get(path, headers=tunnel("198.51.100.1"))
        assert response.status_code == 200
        assert "www-authenticate" not in response.headers
    assert client.get("/docs").status_code == 404


def test_proxy_rewrite_diagnostic_logged_once(intake_spy, caplog):
    # Simulates uvicorn --proxy-headers: peer already rewritten to
    # the visitor IP. Must stay blocked, and log a hint once.
    client = make_client(client_host="198.51.100.1", admin_enabled=False,
                         allow_tunnel_ai=True)

    with caplog.at_level("WARNING", logger="app.security"):
        for _ in range(2):
            response = client.post("/intake/analyze", data=AI_FORM,
                                   headers=tunnel("198.51.100.1"))
            assert response.status_code == 503

    hints = [r for r in caplog.records if "no-proxy-headers" in r.message]
    assert len(hints) == 1
    assert intake_spy == []
