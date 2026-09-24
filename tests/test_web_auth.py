import time

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import web_auth
from app.config import settings

EXTERNAL = {"X-Forwarded-For": "1.2.3.4"}


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(web_auth, "SECRET_FILE", tmp_path / "secret")
    monkeypatch.setattr(web_auth, "_secret", None)
    monkeypatch.setattr(web_auth, "_fails", {})
    monkeypatch.setattr(settings, "web_auth_hash", bcrypt.hashpw(b"pw", bcrypt.gensalt(4)).decode())
    app = FastAPI()

    @app.get("/")
    async def index() -> dict:
        return {"ok": True}

    @app.get("/manifest.webmanifest")
    async def manifest() -> dict:
        return {}

    app.add_middleware(web_auth.WebAuth)
    return TestClient(app, base_url="https://testserver")


def test_local_requests_pass(client: TestClient) -> None:
    assert client.get("/").status_code == 200


def test_external_page_redirects_to_login(client: TestClient) -> None:
    resp = client.get("/", headers={**EXTERNAL, "Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/login")


def test_external_api_401(client: TestClient) -> None:
    assert client.get("/", headers=EXTERNAL).status_code == 401


def test_manifest_is_public(client: TestClient) -> None:
    assert client.get("/manifest.webmanifest", headers=EXTERNAL).status_code == 200


def test_login_sets_year_cookie_and_lets_in(client: TestClient) -> None:
    resp = client.post("/login", data={"username": "Egor", "password": "pw", "next": "/"},
                       headers=EXTERNAL, follow_redirects=False)
    assert resp.status_code == 303
    assert "Max-Age=31536000" in resp.headers["set-cookie"]
    assert client.get("/", headers=EXTERNAL).status_code == 200


def test_wrong_password_and_rate_limit(client: TestClient) -> None:
    for _ in range(web_auth.MAX_FAILS):
        assert client.post("/login", data={"username": "egor", "password": "x"}, headers=EXTERNAL).status_code == 401
    assert client.post("/login", data={"username": "egor", "password": "pw"}, headers=EXTERNAL).status_code == 429


def test_forged_or_expired_token_rejected() -> None:
    web_auth._secret = b"k"
    assert web_auth.valid(f"{int(time.time()) + 100}.deadbeef") is None
    assert web_auth.valid(web_auth._sign(int(time.time()) - 1)) is None
    assert web_auth.valid(web_auth._sign(int(time.time()) + 100)) is not None
    web_auth._secret = None


def test_open_redirect_blocked() -> None:
    assert web_auth._safe_next("//evil.com") == "/"
    assert web_auth._safe_next("https://evil.com") == "/"
