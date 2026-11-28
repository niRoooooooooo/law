
"""Phase P0A.2: access security tests (no real LLM, no real data/)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import SecurityConfigError, SecuritySettings
from app.main import create_app
from app.routes import intake as intake_route
from app.routes import legal_sources as legal_sources_route
from app.schemas.intake import IntakeResult
from app.security import (
    MAX_ADMIN_SESSIONS,
    AccessControlMiddleware,
    AdminSessionStore,
)


ADMIN_KEY = "test-admin-key-not-real-0123456789abcdef"
COOKIE = "__Host-torlaw_admin"
TUNNEL = {"CF-Connecting-IP": "203.0.113.5", "CF-Ray": "abc123"}
CSRF = {"X-Admin-Request": "1"}


def make_settings(**overrides) -> SecuritySettings:
    values = dict(
        admin_enabled=True,
        admin_key=ADMIN_KEY,
        admin_session_minutes=30,
        admin_cookie_secure=True,
        debug=False,
    )
    values.update(overrides)
    return SecuritySettings(**values)


def make_client(client_host="203.0.113.9", **overrides) -> TestClient:
    # https base URL so the Secure cookie is stored and resent.
    # Default client host is non-loopback (a public visitor).
    return TestClient(
        create_app(make_settings(**overrides)),
        base_url="https://testserver",
        client=(client_host, 50000),
        follow_redirects=False,
    )


def login(client: TestClient, key: str = ADMIN_KEY):
    return client.post("/admin/login", data={"admin_key": key})


@pytest.fixture
def fake_repository(monkeypatch):

    class FakeRepository:
        def __init__(self):
            self.added = 0

        def list_documents(self):
            return []

        def add_document(self, *args, **kwargs):
            self.added += 1
            raise ValueError("fake repository")

    repo = FakeRepository()
    monkeypatch.setattr(legal_sources_route, "repository", repo)
    return repo


@pytest.fixture
def intake_spy(monkeypatch):
    calls = []

    async def fake_intake(text, agreement_type="unknown"):
        calls.append(text)
        return IntakeResult(agreement_type="house_rent", language="en")

    monkeypatch.setattr(intake_route, "run_intake_agent", fake_intake)
    return calls


AI_FORM = {
    "agreement_type": "house_rent",
    "agreement_text": "1. Rent\nThe tenant pays rent monthly.",
}


# ------------------------------------------
# Public website is open (no site-wide auth)
# ------------------------------------------

@pytest.mark.parametrize("path", ["/", "/review", "/static/css/style.css"])
def test_public_pages_open_without_auth(path):
    response = make_client().get(path)

    assert response.status_code == 200
    assert "www-authenticate" not in response.headers


def test_public_preview_open_without_auth():
    response = make_client().post(
        "/reviews/preview",
        data={"agreement_type": "house_rent", "agreement_text": "Hello"},
    )
    assert response.status_code == 200


def test_legal_sources_button_still_on_review_page():
    response = make_client().get("/review")
    assert 'href="/admin/laws"' in response.text


# ------------------------------------------
# Admin disabled switch
# ------------------------------------------

@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/admin/login"),
        ("POST", "/admin/login"),
        ("GET", "/admin/laws"),
        ("GET", "/admin/legal-sources/"),
    ],
)
def test_admin_disabled_returns_404(method, path):
    client = make_client(admin_enabled=False)

    response = client.request(
        method, path, headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 404


def test_admin_disabled_by_default():
    assert SecuritySettings.from_env({}).admin_enabled is False


# ------------------------------------------
# Admin login flow
# ------------------------------------------

def test_admin_page_redirects_to_login_without_session():
    response = make_client().get("/admin/laws")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_page_renders_without_secrets():
    response = make_client().get("/admin/login")

    assert response.status_code == 200
    assert 'name="admin_key"' in response.text
    assert ADMIN_KEY not in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "key", ["wrong-key", "", ADMIN_KEY + "x", ADMIN_KEY[:-1], "ключ-é"]
)
def test_login_with_wrong_key_fails(key):
    client = make_client()

    response = login(client, key)

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert ADMIN_KEY not in response.text
    assert client.get("/admin/laws").status_code == 303


def test_login_sets_secure_short_lived_cookie():
    response = login(make_client())

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/laws"

    cookie = response.headers["set-cookie"]
    lowered = cookie.lower()

    assert cookie.startswith(COOKIE + "=")
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert "max-age=1800" in lowered
    assert "domain" not in lowered
    assert ADMIN_KEY not in cookie


def test_admin_page_served_after_login_without_key():
    client = make_client()
    login(client)

    response = client.get("/admin/laws")

    assert response.status_code == 200
    assert "Legal Sources Admin" in response.text
    assert ADMIN_KEY not in response.text
    assert response.headers["x-frame-options"] == "DENY"


def test_login_page_redirects_when_already_signed_in():
    client = make_client()
    login(client)

    response = client.get("/admin/login")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/laws"


def test_session_expires():
    client = make_client()
    login(client)

    store = client.app.state.admin_sessions
    real_clock = store._clock
    store._clock = lambda: real_clock() + 30 * 60 + 1

    assert client.get("/admin/laws").status_code == 303


def test_logout_revokes_session(fake_repository):
    client = make_client()
    login(client)
    token = client.cookies.get(COOKIE)

    response = client.post("/admin/logout", headers=CSRF)

    assert response.status_code == 200
    assert client.get("/admin/laws").status_code == 303

    # The old token is dead on the server, not just deleted locally
    client.cookies.set(COOKIE, token)
    assert client.get("/admin/legal-sources/").status_code == 403


def test_logout_requires_csrf_header():
    client = make_client()
    login(client)

    assert client.post("/admin/logout").status_code == 403
    assert client.get("/admin/laws").status_code == 200


def test_forged_session_cookie_rejected(fake_repository):
    client = make_client()
    client.cookies.set(COOKIE, "forged-token-value")

    assert client.get("/admin/laws").status_code == 303
    assert client.get("/admin/legal-sources/").status_code == 403


def test_session_store_is_bounded():
    store = AdminSessionStore(lifetime_seconds=60)
    tokens = [store.create() for _ in range(MAX_ADMIN_SESSIONS + 5)]

    assert len(store._sessions) == MAX_ADMIN_SESSIONS
    assert not store.is_valid(tokens[0])
    assert store.is_valid(tokens[-1])


def test_session_tokens_not_stored_in_plain_text():
    store = AdminSessionStore(lifetime_seconds=60)
    token = store.create()

    assert token not in store._sessions


# ------------------------------------------
# Admin API: session or X-Admin-Key
# ------------------------------------------

def test_admin_api_rejects_anonymous(fake_repository):
    response = make_client().get("/admin/legal-sources/")

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin authentication required."}


@pytest.mark.parametrize("key", ["wrong", ADMIN_KEY + "x", "ключ"])
def test_admin_api_rejects_wrong_key(key, fake_repository):
    # Non-ASCII keys are sent as raw UTF-8 bytes
    response = make_client().get(
        "/admin/legal-sources/",
        headers=[(b"X-Admin-Key", key.encode("utf-8"))],
    )

    assert response.status_code == 403


def test_admin_api_accepts_admin_key_header(fake_repository):
    response = make_client().get(
        "/admin/legal-sources/", headers={"X-Admin-Key": ADMIN_KEY}
    )

    assert response.status_code == 200
    assert response.json() == {"total": 0, "documents": []}


def test_admin_api_accepts_session(fake_repository):
    client = make_client()
    login(client)

    response = client.get("/admin/legal-sources/")

    assert response.status_code == 200


def test_session_post_requires_csrf_header(fake_repository):
    client = make_client()
    login(client)

    form = {
        "title": "Demo", "jurisdiction": "BD",
        "agreement_type": "house_rent", "is_demo": "true",
    }
    files = {"file": ("x.docx", b"PK\x03\x04", "application/octet-stream")}

    blocked = client.post("/admin/legal-sources/", data=form, files=files)

    assert blocked.status_code == 403
    assert fake_repository.added == 0

    allowed = client.post(
        "/admin/legal-sources/", data=form, files=files, headers=CSRF
    )

    # Passes auth and reaches the repository (fake raises -> 422)
    assert allowed.status_code == 422
    assert fake_repository.added == 1


def test_admin_key_post_does_not_need_csrf_header(fake_repository):
    response = make_client().post(
        "/admin/legal-sources/",
        headers={"X-Admin-Key": ADMIN_KEY},
        data={"title": "Demo", "jurisdiction": "BD",
              "agreement_type": "house_rent"},
        files={"file": ("x.txt", b"hello", "text/plain")},
    )

    # Reaches route validation (unsupported extension)
    assert response.status_code == 400


def test_admin_key_in_query_string_not_accepted(fake_repository):
    response = make_client().get(
        f"/admin/legal-sources/?x_admin_key={ADMIN_KEY}"
    )
    assert response.status_code == 403


# ------------------------------------------
# Gates run before the request body is read
# ------------------------------------------

def run_middleware(path, method="POST", headers=(), client=("203.0.113.9", 1)):
    """Call the middleware directly with a body that must not be read."""
    events = {"body_read": False, "inner_called": False, "status": None}

    async def inner_app(scope, receive, send):
        events["inner_called"] = True

    async def receive():
        events["body_read"] = True
        return {"type": "http.request", "body": b"x" * 10, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            events["status"] = message["status"]

    settings = make_settings()
    middleware = AccessControlMiddleware(
        inner_app, settings, AdminSessionStore(60)
    )

    scope = {
        "type": "http", "method": method, "path": path,
        "raw_path": path.encode(), "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "client": client, "server": ("testserver", 443),
        "scheme": "https", "root_path": "", "app": None,
    }

    asyncio.run(middleware(scope, receive, send))
    return events


@pytest.mark.parametrize(
    "path",
    ["/admin/legal-sources/", "/admin/legal-sources/abc/replace"],
)
def test_admin_upload_rejected_before_body_read(path):
    events = run_middleware(path)

    assert events["status"] == 403
    assert events["body_read"] is False
    assert events["inner_called"] is False


def test_public_ai_request_rejected_before_body_read():
    events = run_middleware("/intake/analyze")

    assert events["status"] == 503
    assert events["body_read"] is False
    assert events["inner_called"] is False


def test_public_routes_pass_through_middleware():
    events = run_middleware("/documents/preview")

    assert events["inner_called"] is True


# ------------------------------------------
# AI endpoints restricted until P0B
# ------------------------------------------

def test_public_ai_request_blocked_before_ollama(intake_spy):
    response = make_client().post("/intake/analyze", data=AI_FORM)

    assert response.status_code == 503
    assert "temporarily unavailable" in response.text
    assert intake_spy == []


def test_tunnelled_ai_request_blocked_even_from_loopback(intake_spy):
    client = make_client(client_host="127.0.0.1")

    response = client.post("/intake/analyze", data=AI_FORM, headers=TUNNEL)

    assert response.status_code == 503
    assert intake_spy == []


def test_direct_local_ai_request_allowed(intake_spy):
    client = make_client(client_host="127.0.0.1")

    response = client.post("/intake/analyze", data=AI_FORM)

    assert response.status_code == 200
    assert len(intake_spy) == 1


def test_admin_session_may_use_ai_through_tunnel(intake_spy):
    client = make_client(client_host="127.0.0.1")
    client.headers.update(TUNNEL)
    login(client)

    response = client.post("/intake/analyze", data=AI_FORM)

    assert response.status_code == 200
    assert len(intake_spy) == 1


def test_ai_blocked_when_admin_disabled_and_public(intake_spy):
    client = make_client(admin_enabled=False)

    assert client.post("/intake/analyze", data=AI_FORM).status_code == 503
    assert intake_spy == []


# ------------------------------------------
# API documentation
# ------------------------------------------

@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_without_debug(path):
    assert make_client(debug=False).get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_available_in_debug(path):
    assert make_client(debug=True).get(path).status_code == 200


# ------------------------------------------
# Startup configuration fails closed
# ------------------------------------------

@pytest.mark.parametrize(
    "environ",
    [
        {"TOR_LAW_ADMIN_ENABLED": "1"},
        {"TOR_LAW_ADMIN_ENABLED": "1", "TOR_LAW_ADMIN_KEY": ""},
        {"TOR_LAW_ADMIN_ENABLED": "1", "TOR_LAW_ADMIN_KEY": "too-short"},
    ],
)
def test_admin_enabled_without_strong_key_prevents_startup(environ):
    with pytest.raises(SecurityConfigError):
        create_app(SecuritySettings.from_env(environ))


def test_admin_disabled_starts_without_key():
    create_app(SecuritySettings.from_env({}))


@pytest.mark.parametrize(
    "environ",
    [
        {"TOR_LAW_ADMIN_ENABLED": "maybe"},
        {"TOR_LAW_DEBUG": "yes please"},
        {"TOR_LAW_ADMIN_SESSION_MINUTES": "0"},
        {"TOR_LAW_ADMIN_SESSION_MINUTES": "10000"},
        {"TOR_LAW_ADMIN_SESSION_MINUTES": "abc"},
    ],
)
def test_invalid_settings_fail_closed(environ):
    with pytest.raises(SecurityConfigError):
        SecuritySettings.from_env(environ)


def test_admin_key_not_in_settings_repr():
    assert ADMIN_KEY not in repr(make_settings())


def test_insecure_cookie_mode_for_local_http():
    client = TestClient(
        create_app(make_settings(admin_cookie_secure=False)),
        follow_redirects=False,
    )

    response = login(client)
    cookie = response.headers["set-cookie"].lower()

    assert cookie.startswith("torlaw_admin=")
    assert "secure" not in cookie
    assert client.get("/admin/laws").status_code == 200
