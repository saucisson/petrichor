"""Tests for the persistent sessions API (spec §4 Gérer, issue #11)."""

from pathlib import Path

import httpx
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}


def build_client(tmp_path: Path, *, session_store: SessionStore | None = None) -> httpx.Client:
    """Build a test client with a session store bound to tmp_path."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    run_store = RunStore()
    if session_store is None:
        session_store = SessionStore(directory=tmp_path / "sessions")
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=None,
        session_store=session_store,
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


def import_model(client: httpx.Client) -> str:
    """Import the Philosophes-3 fixture and return its model id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def test_create_session_returns_201_with_location(tmp_path: Path) -> None:
    """POST /v1/sessions creates a session: 201 + Location (spec §4)."""
    client = build_client(tmp_path)
    model_id = import_model(client)
    response = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert response.status_code == 201
    assert response.headers["Location"].startswith("/v1/sessions/ses_")
    body = response.json()
    assert body["model_id"] == model_id
    assert body["run_ids"] == []


def test_create_session_unknown_model_returns_404(tmp_path: Path) -> None:
    """A session for an unknown model is 404."""
    client = build_client(tmp_path)
    response = client.post("/v1/sessions", json={"model_id": "mdl_unknown"}, headers=JSON_HEADERS)
    assert response.status_code == 404
    assert response.json()["extensions"]["reason"] == "model_not_found"


def test_list_sessions_returns_all_in_creation_order(tmp_path: Path) -> None:
    """GET /v1/sessions lists every session."""
    client = build_client(tmp_path)
    model_id = import_model(client)
    for _ in range(3):
        client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    response = client.get("/v1/sessions", headers=AUTH_HEADERS)
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 3
    created_ats = [s["created_at"] for s in sessions]
    assert created_ats == sorted(created_ats)


def test_get_session_returns_full_state(tmp_path: Path) -> None:
    """GET /v1/sessions/{sid} returns the full serialized state."""
    client = build_client(tmp_path)
    model_id = import_model(client)
    created = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS).json()
    session_id = created["session_id"]
    response = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


def test_get_session_unknown_returns_404(tmp_path: Path) -> None:
    """An unknown session id is 404."""
    client = build_client(tmp_path)
    response = client.get("/v1/sessions/ses_unknown", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_session_journal_is_timestamped_and_append_only(tmp_path: Path) -> None:
    """GET /v1/sessions/{sid}/journal exports the timestamped append-only journal."""
    client = build_client(tmp_path)
    model_id = import_model(client)
    created = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS).json()
    session_id = created["session_id"]
    response = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["event"] == "session_created"
    assert entries[0]["timestamp"] > 0
    assert entries[0]["detail"]["model_id"] == model_id


def test_session_survives_process_restart(tmp_path: Path) -> None:
    """A session created by one store instance is visible to a new instance (spec §4)."""
    directory = tmp_path / "sessions"
    client1 = build_client(tmp_path, session_store=SessionStore(directory=directory))
    model_id = import_model(client1)
    created = client1.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS).json()
    session_id = created["session_id"]

    # Simulate a process restart: a brand-new store on the same directory.
    store2 = SessionStore(directory=directory)
    client2 = build_client(tmp_path, session_store=store2)
    response = client2.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    assert response.json()["model_id"] == model_id

    journal = client2.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
    assert journal.status_code == 200
    assert len(journal.json()) == 1


def test_sessions_require_auth(tmp_path: Path) -> None:
    """Every session endpoint requires the X-API-Key header."""
    client = build_client(tmp_path)
    assert client.get("/v1/sessions").status_code == 401
    assert client.post("/v1/sessions", json={"model_id": "mdl_x"}).status_code == 401
