"""The agent loop accepts only answers grounded in what its tools returned."""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.incident_agent import run_agent  # noqa: E402
from app.services.muse_client import AgentError  # noqa: E402

FIXES = json.loads((BACKEND_DIR.parent / "assets" / "fixes" / "library.json").read_text())["fixes"]

BUNDLE = {
    "incident": {"id": 7, "source_start_ms": 23940, "state": "alerted", "decision_source": "keras", "fall_probability": 0.994},
    "location": {"source": "Authored location", "x": 2.0, "z": -3.1, "zone_id": "stairs_approach", "zone_label": "Foot of the stairs", "fall_exposure": True},
    "imu_summary": {"peak_svm_g": 5.18, "still_after_impact": True},
    "frame": {"status": "Available", "id": "S2-024000", "label": "Rendered frame", "scene": "home1 v1", "alignment_error_ms": 60},
    "sam": {"id": 3, "status": "Complete", "prompt": "stairs", "boxes": [{"x1": 66}], "box_area_fraction": 0.29, "reason": None},
    "recent": [],
    "fixes": FIXES,
}


def tool_call(n, name, **args):
    return {"id": f"call_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def scripted(*replies):
    """A chat stand-in that returns the given assistant messages in order and records each call."""
    seen = []

    def chat(messages, tools, tool_choice, timeout):
        seen.append({"tool_choice": tool_choice, "messages": list(messages)})
        return {"message": replies[min(len(seen), len(replies)) - 1], "usage": {"total_tokens": 100}}
    chat.seen = seen
    return chat


GATHER = {"content": "", "tool_calls": [
    tool_call(1, "get_event_context", incident_id=7),
    tool_call(2, "get_frame_analysis", incident_id=7),
    tool_call(3, "get_environment_fixes", hazard="stairs"),
]}


def final(**answer):
    base = {
        "summary": "A fall was detected at the foot of the stairs in the simulated home.",
        "observations": [{"text": "Impact of 5.18 g followed by stillness.", "evidence_ids": ["imu:7"]},
                         {"text": "SAM found stairs in the rendered frame.", "evidence_ids": ["frame:S2-024000", "sam:3"]}],
        "uncertainties": ["The data cannot show why the person fell."],
        "recommendation_ids": ["stairs_handrail_both_sides", "stairs_motion_lighting"],
    }
    return {"content": json.dumps({**base, **answer}), "tool_calls": None}


def test_a_grounded_answer_is_accepted_with_its_tool_log():
    result = run_agent(BUNDLE, scripted(GATHER, final()), max_tool_calls=5, timeout_seconds=10)
    assert result.status == "Complete", result.reason
    assert [t["tool"] for t in result.tool_log] == ["get_event_context", "get_frame_analysis", "get_environment_fixes"]
    assert result.tool_log[0]["evidence_ids"] == ["imu:7", "location:stairs_approach", "wearer:7"]
    assert result.result["recommendation_ids"] == ["stairs_handrail_both_sides", "stairs_motion_lighting"]
    assert result.usage == {"total_tokens": 200}


def test_citing_evidence_no_tool_returned_fails():
    answer = final(observations=[{"text": "The floor was wet.", "evidence_ids": ["photo:wet-floor"]}])
    result = run_agent(BUNDLE, scripted(GATHER, answer), max_tool_calls=5, timeout_seconds=10)
    assert (result.status, result.result) == ("Failed", None)
    assert "photo:wet-floor" in result.reason


def test_recommending_a_fix_outside_the_library_fails():
    result = run_agent(BUNDLE, scripted(GATHER, final(recommendation_ids=["install_elevator"])), max_tool_calls=5, timeout_seconds=10)
    assert result.status == "Failed" and "install_elevator" in result.reason


def test_recommendations_need_a_get_environment_fixes_call_first():
    gather_without_fixes = {"content": "", "tool_calls": [tool_call(1, "get_event_context", incident_id=7)]}
    answer = final(observations=[{"text": "Impact detected.", "evidence_ids": ["imu:7"]}])
    result = run_agent(BUNDLE, scripted(gather_without_fixes, answer), max_tool_calls=5, timeout_seconds=10)
    assert result.status == "Failed" and "stairs_handrail_both_sides" in result.reason


def test_the_tool_budget_forces_an_answer():
    greedy = {"content": "", "tool_calls": [tool_call(n, "get_event_context", incident_id=7) for n in range(3)]}
    only_imu = [{"text": "Impact detected.", "evidence_ids": ["imu:7"]}]
    chat = scripted(greedy, greedy, final(observations=only_imu, recommendation_ids=[]))
    result = run_agent(BUNDLE, chat, max_tool_calls=5, timeout_seconds=10)
    assert result.status == "Complete", result.reason
    assert len(result.tool_log) == 6
    assert sum(1 for t in result.tool_log if t["evidence_ids"]) == 5
    assert [c["tool_choice"] for c in chat.seen] == ["auto", "auto", "none"]


def test_api_errors_and_timeouts_are_recorded_not_hidden():
    def failing(messages, tools, tool_choice, timeout):
        raise AgentError("TimedOut", "no response within 3.0 s")
    result = run_agent(BUNDLE, failing, max_tool_calls=5, timeout_seconds=10)
    assert (result.status, result.reason, result.result) == ("TimedOut", "no response within 3.0 s", None)


def test_an_answer_that_is_not_json_fails():
    result = run_agent(BUNDLE, scripted(GATHER, {"content": "The person fell on the stairs.", "tool_calls": None}),
                       max_tool_calls=5, timeout_seconds=10)
    assert result.status == "Failed" and "no JSON object" in result.reason


def test_an_answer_cut_off_at_the_token_limit_says_so():
    cut = {"content": '{"summary": "A fall was detected at the foot', "tool_calls": None}

    def chat(messages, tools, tool_choice, timeout):
        if messages[-1]["role"] == "user":
            return {"message": GATHER, "usage": {}}
        return {"message": cut, "finish_reason": "length", "usage": {}}
    result = run_agent(BUNDLE, chat, max_tool_calls=5, timeout_seconds=10)
    assert (result.status, result.reason) == ("Failed", "invalid output: the answer was cut off at the token limit")
    assert result.raw_answer["content"].startswith('{"summary"')
