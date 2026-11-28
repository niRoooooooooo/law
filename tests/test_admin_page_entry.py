"""
Phase F1: every admin-page visit needs the administrator key.

Login issues a single-use page entry; the first /admin/laws load
consumes it. Refresh / revisit revokes the session server-side.
AJAX calls from the open page keep working. (No real data/.)
"""

import pytest
from fastapi.testclient import TestClient

from app.config import SecuritySettings
from app.main import create_app
from app.routes import legal_sources as legal_sources_route
from app.security import AdminSessionStore
from app.services.legal_repository import LegalRepository


ADMIN_KEY = "test-admin-key-not-real-0123456789abcdef"
COOKIE = "__Host-torlaw_admin"
CSRF = {"X-Admin-Request": "1"}
TUNNEL = {"CF-Connecting-IP": "198.51.100.8", "CF-Ray": "abc"}


def make_client(client_host="203.0.113.9", **overrides) -> TestClient:
    values = dict(admin_enabled=True, admin_key=ADMIN_KEY)
    values.update(overrides)
    return TestClient(
        create_app(SecuritySettings(**values)),
        base_url="https://testserver",
        client=(client_host, 50000),
        follow_redirects=False,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repository = LegalRepository(data_dir=tmp_path)
    monkeypatch.setattr(legal_sources_route, "repository", repository)
    return repository


def login(client, key=ADMIN_KEY):
    return client.post("/admin/login", data={"admin_key": key})


def assert_sent_to_login(response):
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


# ------------------------------------------
# Session store
# ------------------------------------------

def test_page_entry_is_single_use_and_revokes_on_reuse():
    store = AdminSessionStore(60)
    token = store.create()

    assert store.consume_page_entry(token) is True
    assert store.is_valid(token) is True          # AJAX still works

    assert store.consume_page_entry(token) is False
    assert store.is_valid(token) is False         # session revoked


@pytest.mark.parametrize("token", [None, "", "unknown", "x" * 200])
def test_page_entry_rejects_bad_tokens(token):
    assert AdminSessionStore(60).consume_page_entry(token) is False


def test_page_entry_rejected_after_expiry():
    now = [1000.0]
    store = AdminSessionStore(60, clock=lambda: now[0])
    token = store.create()
    now[0] += 61

    assert store.consume_page_entry(token) is False


# ------------------------------------------
# Browser flow
# ------------------------------------------

def test_manage_link_without_login_goes_to_login():
    assert_sent_to_login(make_client().get("/admin/laws"))


def test_first_load_after_login_is_served():
    client = make_client()
    response = login(client)
    assert response.headers["location"] == "/admin/laws"

    page = client.get("/admin/laws")
    assert page.status_code == 200
    assert "Legal Sources Admin" in page.text
    assert page.headers["cache-control"] == "no-store"


def test_refresh_requires_key_again(repo):
    client = make_client()
    login(client)
    token = client.cookies.get(COOKIE)
    assert client.get("/admin/laws").status_code == 200

    refresh = client.get("/admin/laws")
    assert_sent_to_login(refresh)
    # Cookie cleared in the browser AND session dead on the server
    assert COOKIE in refresh.headers.get("set-cookie", "")
    client.cookies.set(COOKIE, token)
    assert client.get("/admin/legal-sources/").status_code == 403


def test_leaving_and_returning_requires_key_again(repo):
    client = make_client()
    login(client)
    assert client.get("/admin/laws").status_code == 200

    # Navigate to public pages, then back to the admin page
    assert client.get("/review").status_code == 200
    assert client.get("/").status_code == 200
    assert_sent_to_login(client.get("/admin/laws"))
    assert client.get("/admin/legal-sources/").status_code == 403


def test_ajax_keeps_working_while_page_is_open(repo):
    client = make_client()
    login(client)
    assert client.get("/admin/laws").status_code == 200

    for _ in range(3):
        assert client.get("/admin/legal-sources/").status_code == 200

    # Still allowed: AJAX does not consume the page entry
    assert client.get("/admin/legal-sources/").status_code == 200


def test_relogin_grants_a_fresh_page_entry(repo):
    client = make_client()
    login(client)
    client.get("/admin/laws")
    assert_sent_to_login(client.get("/admin/laws"))

    login(client)
    assert client.get("/admin/laws").status_code == 200


def test_wrong_key_gives_no_page_entry():
    client = make_client()
    response = login(client, "wrong-key-wrong-key-wrong-key-wrong")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert_sent_to_login(client.get("/admin/laws"))


def test_logout_revokes_immediately(repo):
    client = make_client()
    login(client)
    assert client.get("/admin/laws").status_code == 200

    assert client.post("/admin/logout", headers=CSRF).status_code == 200
    assert client.get("/admin/legal-sources/").status_code == 403
    assert_sent_to_login(client.get("/admin/laws"))


def test_login_before_page_load_then_login_page_revokes(repo):
    client = make_client()
    login(client)
    assert client.get("/admin/login").status_code == 200

    assert_sent_to_login(client.get("/admin/laws"))


def test_page_entry_per_session_independent(repo):
    first, second = make_client(), make_client()
    # Separate apps would have separate stores; share one app instead
    second = TestClient(first.app, base_url="https://testserver",
                        client=("203.0.113.10", 50000),
                        follow_redirects=False)
    login(first)
    login(second)

    assert first.get("/admin/laws").status_code == 200
    assert second.get("/admin/laws").status_code == 200
    assert_sent_to_login(first.get("/admin/laws"))
    # Second admin's open page keeps working
    assert second.get("/admin/legal-sources/").status_code == 200


# ------------------------------------------
# Unchanged protections
# ------------------------------------------

def test_api_key_clients_unaffected_by_page_entry(repo):
    client = make_client()
    headers = {"X-Admin-Key": ADMIN_KEY}

    for _ in range(3):
        assert client.get("/admin/legal-sources/",
                          headers=headers).status_code == 200
    # The key alone never opens the browser page
    assert_sent_to_login(client.get("/admin/laws", headers=headers))


def test_csrf_header_still_required_for_session_writes(repo):
    client = make_client()
    login(client)
    client.get("/admin/laws")

    response = client.post("/admin/legal-sources/", data={"title": "x"})
    assert response.status_code == 403


def test_public_pages_never_need_admin_auth():
    client = make_client()
    for path in ("/", "/review", "/static/css/style.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert "www-authenticate" not in response.headers


def test_no_secrets_in_admin_responses(repo):
    client = make_client()
    login_response = login(client)
    token = client.cookies.get(COOKIE)
    page = client.get("/admin/laws")

    for response in (login_response, page):
        assert ADMIN_KEY not in response.text
        assert token not in response.text
        assert token not in response.headers.get("location", "")


def test_cookie_flags_preserved():
    cookie = login(make_client()).headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie


def test_tunnel_admin_flow_same_rules(repo):
    client = make_client(client_host="127.0.0.1")
    client.headers.update(TUNNEL)

    assert_sent_to_login(client.get("/admin/laws"))
    login(client)
    assert client.get("/admin/laws").status_code == 200
    assert_sent_to_login(client.get("/admin/laws"))
