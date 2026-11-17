"""Tests for the question-card arbitration API (spec §4.8, §12.7, issue #16).

Accept / refuse / verify-first on a card; a revision records a NEW decision
and the previous one stays in the history and the journal (avant/après).
No automatic fusion of questions ever happens here (convergence is P34).
"""

from pathlib import Path

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}


def build_client(
    tmp_path: Path, *, arbitration_store: ArbitrationStore | None = None
) -> httpx.Client:
    """Build a test client with session and arbitration stores bound to tmp_path."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    session_store = SessionStore(directory=tmp_path / "sessions")
    if arbitration_store is None:
        arbitration_store = ArbitrationStore(directory=tmp_path / "arbitrations")
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        session_store=session_store,
        arbitration_store=arbitration_store,
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


def create_session(client: httpx.Client) -> str:
    """Import Philosophes-3 and create one session; return its id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    created = client.post(
        "/v1/sessions", json={"model_id": response.json()["id"]}, headers=JSON_HEADERS
    )
    assert created.status_code == 201
    return created.json()["session_id"]


def post_decision(
    client: httpx.Client, session_id: str, question_id: str, decision: str
) -> httpx.Response:
    """POST one arbitration decision on a question card."""
    return client.post(
        f"/v1/sessions/{session_id}/arbitration-requests",
        json={"question_id": question_id, "decision": decision},
        headers=JSON_HEADERS,
    )


class TestArbitrationDecisions:
    def test_accept_refuse_and_verify_first_are_recorded(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        for question_id, decision in (
            ("q_aaa", "accept"),
            ("q_bbb", "refuse"),
            ("q_ccc", "verify-first"),
        ):
            response = post_decision(client, session_id, question_id, decision)
            assert response.status_code == 201
            record = response.json()["record"]
            assert record["question_id"] == question_id
            assert record["decision"] == decision
            assert record["previous_decision"] is None

    def test_revision_keeps_the_previous_decision_in_history_and_journal(
        self, tmp_path: Path
    ) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        first = post_decision(client, session_id, "q_m3", "verify-first")
        assert first.status_code == 201
        second = post_decision(client, session_id, "q_m3", "accept")
        assert second.status_code == 201
        record = second.json()["record"]
        assert record["decision"] == "accept"
        assert record["previous_decision"] == "verify-first"
        history = second.json()["question_history"]
        assert [r["decision"] for r in history] == ["verify-first", "accept"]

        journal = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
        decisions = [e for e in journal.json() if e["event"] == "arbitration_decided"]
        assert len(decisions) == 2
        assert decisions[0]["detail"]["previous_decision"] is None
        assert decisions[1]["detail"]["previous_decision"] == "verify-first"
        assert decisions[1]["detail"]["decision"] == "accept"

    def test_invalid_decision_is_422(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        response = post_decision(client, session_id, "q_m3", "maybe")
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_arbitration_request"

    def test_unknown_session_is_404(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        response = post_decision(client, "ses_unknown", "q_m3", "accept")
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"

    def test_get_arbitrations_returns_the_full_history(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        post_decision(client, session_id, "q_a", "accept")
        post_decision(client, session_id, "q_b", "refuse")
        post_decision(client, session_id, "q_a", "verify-first")
        response = client.get(f"/v1/sessions/{session_id}/arbitrations", headers=AUTH_HEADERS)
        assert response.status_code == 200
        decisions = response.json()["decisions"]
        assert [(d["question_id"], d["decision"]) for d in decisions] == [
            ("q_a", "accept"),
            ("q_b", "refuse"),
            ("q_a", "verify-first"),
        ]

    def test_no_automatic_fusion_of_questions(self, tmp_path: Path) -> None:
        """Two decided cards stay two distinct entries - convergence is P34, never automatic."""
        client = build_client(tmp_path)
        session_id = create_session(client)
        post_decision(client, session_id, "q_a", "accept")
        post_decision(client, session_id, "q_b", "accept")
        response = client.get(f"/v1/sessions/{session_id}/arbitrations", headers=AUTH_HEADERS)
        decisions = response.json()["decisions"]
        assert len(decisions) == 2
        assert {d["question_id"] for d in decisions} == {"q_a", "q_b"}

    def test_arbitrations_survive_process_restart(self, tmp_path: Path) -> None:
        directory = tmp_path / "arbitrations"
        client1 = build_client(tmp_path, arbitration_store=ArbitrationStore(directory=directory))
        session_id = create_session(client1)
        post_decision(client1, session_id, "q_a", "accept")
        post_decision(client1, session_id, "q_a", "refuse")

        store2 = ArbitrationStore(directory=directory)
        client2 = build_client(tmp_path, arbitration_store=store2)
        response = client2.get(f"/v1/sessions/{session_id}/arbitrations", headers=AUTH_HEADERS)
        assert response.status_code == 200
        decisions = response.json()["decisions"]
        assert [d["decision"] for d in decisions] == ["accept", "refuse"]

    def test_arbitration_endpoints_require_auth(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        assert (
            client.post(
                f"/v1/sessions/{session_id}/arbitration-requests",
                json={"question_id": "q_a", "decision": "accept"},
            ).status_code
            == 401
        )
        assert client.get(f"/v1/sessions/{session_id}/arbitrations").status_code == 401

    def test_arbitration_narration_is_pinned(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        session_id = create_session(client)
        with structlog.testing.capture_logs() as logs:
            post_decision(client, session_id, "q_a", "accept")
        decided = next(e for e in logs if e["event"] == "api_arbitration_decided")
        assert decided["question_id"] == "q_a"
        assert decided["decision"] == "accept"
        assert decided["previous_decision"] is None
        store_event = next(e for e in logs if e["event"] == "arbitration_decided")
        assert store_event["decision"] == "accept"
