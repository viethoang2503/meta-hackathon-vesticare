"""
incident_agent.py

A bounded Muse Spark tool loop that explains one fall incident and picks
environmental changes from the curated library. Pure: it reads a prepared
evidence bundle and calls an injected `chat` function, so tests run offline.

Every tool result carries evidence ids. The final answer is accepted only if
each observation cites ids that a tool actually returned in this run and every
recommendation is a library fix that get_environment_fixes returned; anything
else is recorded as Failed with the reason. The policy (who is alerted, when)
never depends on this output.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from app.services.muse_client import AgentError

SYSTEM_PROMPT = """You explain a detected fall to a caregiver using only the evidence the tools return.
Rules:
- Call tools to gather evidence before answering. Cite evidence only by the evidence_id values tools returned.
- Location comes from an authored scenario route and the camera frame is a rendered simulation; say so if you mention them.
- Do not diagnose, name medical conditions or give medical advice. Recommend only environmental or routine changes, and only by fix id from get_environment_fixes.
- State what the evidence cannot show (for example why the person fell) as an uncertainty.
- Be brief: at most 4 observations and 3 uncertainties, each under 25 words.
Answer with one JSON object and nothing else:
{"summary": "<= 2 sentences", "observations": [{"text": "...", "evidence_ids": ["..."]}], "uncertainties": ["..."], "recommendation_ids": ["..."]}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_event_context",
        "description": "IMU summary, detector decision, wearer response state and authored location for the incident.",
        "parameters": {"type": "object", "properties": {"incident_id": {"type": "integer"}}, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "get_frame_analysis",
        "description": "The glasses frame nearest the incident and the SAM segmentation run on it.",
        "parameters": {"type": "object", "properties": {"incident_id": {"type": "integer"}}, "required": ["incident_id"]}}},
    {"type": "function", "function": {
        "name": "get_recent_incidents",
        "description": "Other fall incidents in the same zone during this session.",
        "parameters": {"type": "object", "properties": {"zone_id": {"type": "string"}}, "required": ["zone_id"]}}},
    {"type": "function", "function": {
        "name": "get_environment_fixes",
        "description": "Curated environmental changes for a hazard type such as stairs, rug or bathroom.",
        "parameters": {"type": "object", "properties": {"hazard": {"type": "string"}}, "required": ["hazard"]}}},
]


@dataclass
class AgentResult:
    status: str  # Complete | Failed | TimedOut
    reason: str | None = None
    result: dict | None = None
    tool_log: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    # Kept for diagnosis when the answer is rejected; never shown as an explanation.
    raw_answer: dict | None = None


class _Tools:
    def __init__(self, bundle: dict):
        self.bundle = bundle
        self.seen_ids: set[str] = set()
        self.fix_ids: set[str] = set()

    def run(self, name: str, args: dict) -> dict:
        b = self.bundle
        inc = b["incident"]
        if name == "get_event_context":
            if args.get("incident_id") != inc["id"]:
                return {"error": f"unknown incident {args.get('incident_id')}"}
            out = {"items": [
                {"evidence_id": f"imu:{inc['id']}", "source": "Scenario-paired IMU recording", **b["imu_summary"],
                 "decision_source": inc["decision_source"], "fall_probability": round(inc["fall_probability"], 3)},
                {"evidence_id": f"location:{b['location']['zone_id']}", **b["location"]},
                {"evidence_id": f"wearer:{inc['id']}", "wearer_response": inc["state"]},
            ]}
        elif name == "get_frame_analysis":
            if args.get("incident_id") != inc["id"]:
                return {"error": f"unknown incident {args.get('incident_id')}"}
            frame, sam = b["frame"], b["sam"]
            items = []
            if frame.get("status") == "Available":
                items.append({"evidence_id": f"frame:{frame['id']}", "label": frame["label"], "scene": frame["scene"],
                              "alignment_error_ms": frame["alignment_error_ms"]})
            if sam:
                items.append({"evidence_id": f"sam:{sam['id']}", "status": sam["status"], "prompt": sam["prompt"],
                              "objects": len(sam["boxes"]), "box_area_fraction": sam.get("box_area_fraction"),
                              "reason": sam.get("reason")})
            out = {"items": items or [{"note": "no frame or segmentation available"}]}
        elif name == "get_recent_incidents":
            zone = args.get("zone_id")
            others = [i for i in b["recent"] if i["zone_id"] == zone]
            out = {"items": [{"evidence_id": f"history:{zone}", "scope": "this session", "count": len(others),
                              "start_times_s": [round(i["source_start_ms"] / 1000, 1) for i in others]}]}
        elif name == "get_environment_fixes":
            fixes = [f for f in b["fixes"] if f["hazard"] == args.get("hazard")]
            self.fix_ids |= {f["id"] for f in fixes}
            return {"fixes": [{"id": f["id"], "title": f["title"], "detail": f["detail"]} for f in fixes]}
        else:
            return {"error": f"unknown tool {name}"}
        self.seen_ids |= {item["evidence_id"] for item in out["items"] if "evidence_id" in item}
        return out


def _parse_final(content: str) -> dict:
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return json.loads(content[start:end + 1])


def _validate(answer: dict, tools: _Tools) -> str | None:
    if not isinstance(answer.get("summary"), str) or not answer["summary"].strip():
        return "summary missing"
    observations = answer.get("observations")
    if not isinstance(observations, list) or not observations:
        return "no observations"
    for obs in observations:
        cited = obs.get("evidence_ids") if isinstance(obs, dict) else None
        if not isinstance(cited, list) or not cited:
            return "an observation cites no evidence"
        unknown = [e for e in cited if e not in tools.seen_ids]
        if unknown:
            return f"cites evidence no tool returned: {unknown}"
    if not all(isinstance(u, str) for u in answer.get("uncertainties", [])):
        return "uncertainties must be strings"
    unknown_fixes = [r for r in answer.get("recommendation_ids", []) if r not in tools.fix_ids]
    if unknown_fixes:
        return f"recommends fixes not returned by get_environment_fixes: {unknown_fixes}"
    return None


def run_agent(bundle: dict, chat, *, max_tool_calls: int, timeout_seconds: float) -> AgentResult:
    inc, loc = bundle["incident"], bundle["location"]
    return run_tool_loop(SYSTEM_PROMPT,
                         f"Explain fall incident {inc['id']} (zone {loc['zone_id']}) and recommend environmental changes.",
                         TOOLS, _Tools(bundle), _validate, chat,
                         max_tool_calls=max_tool_calls, timeout_seconds=timeout_seconds)


def run_tool_loop(system_prompt: str, user_prompt: str, tools_spec: list, tools, validate, chat, *,
                  max_tool_calls: int, timeout_seconds: float) -> AgentResult:
    """One bounded tool conversation. `tools.run(name, args)` answers a call, `validate(answer, tools)`
    returns why the final answer is unusable or None. A tool that refuses or fails says so in its
    output; the log keeps that note next to the call."""
    deadline = time.monotonic() + timeout_seconds
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    log, usage, calls = [], {}, 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return AgentResult("TimedOut", f"no answer within {timeout_seconds} s", tool_log=log, usage=usage)
        try:
            reply = chat(messages, tools_spec, "auto" if calls < max_tool_calls else "none", remaining)
        except AgentError as err:
            return AgentResult(err.status, err.reason, tool_log=log, usage=usage)
        for k, v in (reply.get("usage") or {}).items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
        message = reply["message"]
        requested = message.get("tool_calls") or []
        if requested and calls < max_tool_calls:
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": requested})
            for call in requested:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if calls >= max_tool_calls:
                    output = {"error": f"tool budget of {max_tool_calls} calls is used up; answer now"}
                else:
                    calls += 1
                    output = tools.run(name, args)
                entry = {"tool": name, "args": args,
                         "evidence_ids": [i["evidence_id"] for i in output.get("items", []) if "evidence_id" in i]}
                entry |= {k: output[k] for k in ("refused", "scheduled", "said") if k in output}
                log.append(entry)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(output)})
            continue
        raw = {"finish_reason": reply.get("finish_reason"), "content": (message.get("content") or "")[:4000]}
        if raw["finish_reason"] == "length":
            return AgentResult("Failed", "invalid output: the answer was cut off at the token limit",
                               tool_log=log, usage=usage, raw_answer=raw)
        try:
            answer = _parse_final(message.get("content") or "")
        except (ValueError, json.JSONDecodeError) as err:
            return AgentResult("Failed", f"invalid output: {err} (finish_reason {raw['finish_reason']})",
                               tool_log=log, usage=usage, raw_answer=raw)
        problem = validate(answer, tools)
        if problem:
            return AgentResult("Failed", f"invalid output: {problem}", tool_log=log, usage=usage, raw_answer=raw)
        return AgentResult("Complete", result=answer, tool_log=log, usage=usage)
