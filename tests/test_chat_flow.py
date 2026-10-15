"""Fast front-door behaviour without Azure calls."""
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mbpoc import mb_chat_api, orchestrator_api
from mbpoc.agents import foundry, hosted, mb_chat, mock_kb
from mbpoc.contracts import Ack, Citation, Decision, Intake, OrchestratorResponse, RiskResult
from mbpoc.events import Tracer


QUESTION = "  Can I work from Spain for 2 months?\nDo not assume approval.  "


def answered():
    return OrchestratorResponse(
        run_id="test", status="ANSWERED", answer="Approved answer",
        risk=RiskResult(citations=[Citation(title="Policy", source="sharepoint", url="https://example.test/policy")]),
    )


@pytest.mark.parametrize("question", [QUESTION, "Vacation days?", "Gifts", "Maternity leave"])
def test_front_door_has_no_model_calls_or_sleep(monkeypatch, question):
    model = Mock(side_effect=AssertionError("MB Chat must not call a model"))
    sleep = Mock(side_effect=AssertionError("MB Chat must not add artificial latency"))
    monkeypatch.setattr(foundry.FoundryRunner, "call", model)
    monkeypatch.setattr(mock_kb, "latency", sleep)
    tr = Tracer("test", lambda e: None)
    started = time.perf_counter()
    intake = mb_chat.intake(question, tr)
    reply = mb_chat.present(answered(), tr)
    assert time.perf_counter() - started < 0.25
    assert not intake.needs_clarification
    assert intake.normalized_query == question
    assert "Approved answer" in reply and "https://example.test/policy" in reply
    model.assert_not_called()
    sleep.assert_not_called()


@pytest.mark.parametrize("question", ["help", " Hi! ", "policy?", "I have a question", "   "])
def test_vague_requests_still_get_clarification(question):
    assert mb_chat.intake(question, Tracer("test", lambda e: None)).needs_clarification


def test_chat_forwards_original_even_if_intake_returns_different_wording(monkeypatch):
    monkeypatch.setattr(mb_chat, "intake", lambda *args: Intake(normalized_query="Changed wording"))
    call = Mock(return_value=answered())
    monkeypatch.setattr(mb_chat, "call_orchestrator", call)
    events = []
    reply = mb_chat_api._run(mb_chat_api.ChatRequest(message=QUESTION), Tracer("test", events.append))
    call.assert_called_once()
    assert call.call_args.args[0] == QUESTION
    assert reply["status"] == "ANSWERED"
    assert set(reply["timings_ms"]) == {"intake", "orchestrator", "presentation", "total"}
    assert all(value >= 0 for value in reply["timings_ms"].values())
    assert any(e["kind"] == "timing" and e["stage"] == "round_trip" for e in events)


@pytest.mark.parametrize("mode", ["local", "foundry"])
def test_a2a_gateway_preserves_question_in_both_modes(monkeypatch, mode):
    monkeypatch.setenv("ORCHESTRATOR_MODE", mode)
    monkeypatch.setattr(orchestrator_api, "API_KEY", "")
    call = Mock(return_value=answered())
    monkeypatch.setattr(orchestrator_api, "handle_request", call)
    monkeypatch.setattr(hosted, "call", call)
    with TestClient(orchestrator_api.app) as client:
        result = client.post("/a2a", json={
            "jsonrpc": "2.0", "id": "test", "method": "message/send",
            "params": {"message": {"parts": [{"kind": "text", "text": QUESTION}]}},
        })
    assert result.status_code == 200
    assert call.call_args.args[0] == QUESTION
    assert len(call.call_args.args) == 3  # No trace-delay argument.


def test_hosted_call_replays_without_sleep_and_keeps_original_timestamps(monkeypatch):
    response = answered()
    response.trace = [{"kind": "status", "agent": "risk", "state": "done", "ts": 123.5, "run_id": "old"}]
    create = Mock(return_value=SimpleNamespace(output_text=response.model_dump_json()))
    monkeypatch.setattr(hosted, "_openai_client", lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(time, "sleep", Mock(side_effect=AssertionError("Trace replay must not sleep")))
    events = []
    hosted.call(QUESTION, "tester", events.append)
    assert json.loads(create.call_args.kwargs["input"])["question"] == QUESTION
    assert events[0]["ts"] == 123.5 and "run_id" not in events[0]


def test_escalation_never_presents_draft_or_promises_real_ticket():
    response = OrchestratorResponse(
        run_id="test", status="ESCALATED",
        risk=RiskResult(draft_answer="DO NOT DISPLAY THIS"),
        decision=Decision(route="ESCALATE", trigger="high_risk"),
        incident=Ack(incident_id="INC1", assignment_group="HR Legal", mock=True),
    )
    reply = mb_chat.present(response, Tracer("test", lambda e: None))
    assert "DO NOT DISPLAY THIS" not in reply
    assert "INC1" in reply and "No real ServiceNow incident" in reply


def test_failed_agent_call_still_emits_duration(monkeypatch):
    runner = object.__new__(foundry.FoundryRunner)
    monkeypatch.setattr(runner, "_create", Mock(side_effect=RuntimeError("model unavailable")))
    events = []
    with pytest.raises(RuntimeError, match="unavailable"):
        runner.call("risk", QUESTION, Tracer("test", events.append))
    assert events[0]["kind"] == "timing" and events[0]["duration_ms"] >= 0
