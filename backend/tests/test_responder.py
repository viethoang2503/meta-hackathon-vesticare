"""The P0 agent: what the checks allow, and the whole path from an alerted incident to a fixed-format
update and a spoken line. The model is a script; the policy, notifier and phone channel are real."""

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fake_esp32  # noqa: E402
from app import database  # noqa: E402
from app.api import sensor as sensor_api  # noqa: E402
from app.database import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import SpatialAnalysis  # noqa: E402
from app.services import spatial_runs as runs_module  # noqa: E402
from app.services.responder import TOOLS, check_say, check_send  # noqa: E402
from app.services.spatial_runs import load_scenario, spatial_runs  # noqa: E402


def facts(state="alerted", sends=None, agent_updates=(), said=(), acknowledged=False):
    return {"incident_id": 7, "state": state, "acknowledged": acknowledged, "sends": sends or {},
            "agent_updates": list(agent_updates), "said": list(said), "evidence": {"event": [], "scene": [], "delivery": []}}


SEEN = {"imu:7", "delivery:7:1"}


@pytest.mark.parametrize("kwargs, contact, ids, reason", [
    ({}, "contact_1", ["imu:7"], None),
    ({"sends": {"contact_1": "unknown"}}, "contact_1", ["imu:7"], None),
    ({"sends": {"contact_1": "simulated"}}, "contact_1", ["imu:7"], None),
    ({}, "contact_3", ["imu:7"], "is not a contact"),
    ({"state": "cancelled_by_wearer"}, "contact_1", ["imu:7"], "cancelled_by_wearer, not alerted"),
    ({}, "contact_1", [], "must list the evidence"),
    ({}, "contact_1", ["imu:99"], "cites evidence no tool returned"),
    ({"sends": {}}, "contact_1", ["imu:7"], "no alert was sent to this contact"),
    ({"sends": {"contact_1": "scheduled"}}, "contact_1", ["imu:7"], "still being sent"),
    ({"sends": {"contact_1": "failed"}}, "contact_1", ["imu:7"], "failed"),
    ({"agent_updates": ["contact_1"]}, "contact_1", ["imu:7"], "already has the agent's update"),
])
def test_an_update_goes_only_to_a_contact_whose_alert_went_out_and_only_once(kwargs, contact, ids, reason):
    sends = {"contact_1": "sent", **kwargs.pop("sends", {"contact_1": "sent"})} if "sends" not in kwargs else kwargs.pop("sends")
    problem = check_send(facts(sends=sends, **kwargs), contact, ids, SEEN)
    assert (problem is None) if reason is None else (reason in problem)


@pytest.mark.parametrize("template, kwargs, reason", [
    ("alert_pending", {"sends": {"contact_1": "scheduled"}}, None),
    ("alert_pending", {"sends": {"contact_1": "sent"}}, "already been confirmed sent"),
    ("alert_pending", {"sends": {}}, "no alert is being sent"),
    ("alert_sent", {"sends": {"contact_1": "sent"}}, None),
    ("alert_sent", {"sends": {"contact_1": "simulated"}}, "no alert has been confirmed sent"),
    ("alert_unconfirmed", {"sends": {"contact_1": "failed"}}, None),
    ("alert_unconfirmed", {"sends": {"contact_1": "unknown"}}, None),
    ("alert_unconfirmed", {"sends": {"contact_1": "failed", "contact_2": "sent"}}, "confirmed sent"),
    ("alert_unconfirmed", {"sends": {"contact_1": "scheduled"}}, "has not failed"),
    ("caregiver_ack", {"acknowledged": True}, None),
    ("caregiver_ack", {"acknowledged": False}, "no caregiver has acknowledged"),
    ("alert_sent", {"sends": {"contact_1": "sent"}, "said": ["alert_sent"]}, "already said"),
    ("alert_sent", {"sends": {"contact_1": "sent"}, "state": "cancelled_by_wearer"}, "not alerted"),
    ("cancel_confirmed", {"sends": {"contact_1": "sent"}}, "not a line the agent may choose"),
    ("ask_ok", {}, "not a line the agent may choose"),
    ("stairs_visible", {}, "not a line the agent may choose"),
])
def test_the_phone_says_a_line_only_when_the_delivery_state_supports_it(template, kwargs, reason):
    problem = check_say(facts(**kwargs), template)
    assert (problem is None) if reason is None else (reason in problem)


def test_the_agent_has_no_tool_that_cancels_escalates_advises_or_sounds_an_alarm():
    assert sorted(t["function"]["name"] for t in TOOLS) == [
        "get_delivery_status", "get_event_context", "get_scene_analysis", "reassure_wearer", "send_message"]


class Notifier:
    channel = "telegram_stand_in"

    def __init__(self):
        self.sent = []

    def configured_contacts(self):
        return {"contact_1", "contact_2"}

    def send(self, contact, text, incident_id, ack_data):
        self.sent.append((contact, text, ack_data))
        return {"status": "sent", "message_id": len(self.sent)}


def call(n, name, **args):
    return {"id": f"call_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class ResponderApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/r.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.saved = (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN,
                      spatial_runs._chat, spatial_runs._notifier_override, spatial_runs._live_wearer_cancel,
                      spatial_runs._grace, spatial_runs._caregiver_ack, spatial_runs._preload_whisper)
        spatial_runs._preload_whisper = False
        database.engine = engine
        database.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        runs_module.LIVE_DATA_DIR = Path(self.tmp.name) / "live"
        sensor_api.DEVICE_TOKEN = "device-secret"
        self.notifier = Notifier()
        spatial_runs._notifier_override = self.notifier
        spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack = 3, 1, 60

    def tearDown(self):
        (database.engine, database.SessionLocal, runs_module.LIVE_DATA_DIR, sensor_api.DEVICE_TOKEN, spatial_runs._chat,
         spatial_runs._notifier_override, spatial_runs._live_wearer_cancel, spatial_runs._grace, spatial_runs._caregiver_ack,
         spatial_runs._preload_whisper) = self.saved
        spatial_runs._clocks.clear()
        self.tmp.cleanup()

    def open_incident(self, client):
        sid = client.post("/api/spatial/live", json={"patient_id": 1, "device_id": "fake-esp32", "source": "fake_esp32",
                                                     "notification_mode": "simulated"}).json()["id"]
        table = load_scenario("S2")
        full = np.column_stack([table.source_ms, table.samples])
        for n, chunk in enumerate(fake_esp32.batches(full, None, 0)):
            r = client.post("/api/sensor/batch", headers={"Authorization": "Bearer device-secret"},
                            json=fake_esp32.payload(chunk, 1, "fake-esp32", "b1", n, 0)).json()
            if r["incidents_opened"]:
                return sid, r["incidents_opened"][0]
        raise AssertionError("S2 opened no incident")

    def wait(self, client, sid, predicate, seconds=12):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            snap = client.get(f"/api/spatial/sessions/{sid}").json()
            if predicate(snap):
                return snap
            time.sleep(0.1)
        raise AssertionError(f"condition not reached; actions {[(a['kind'], a['target'], a['detail'].get('status')) for a in snap['actions']]}, "
                             f"spoken {snap['wearer']['spoken']}, agent {self.agent_row(sid)[:3]}")

    def agent_row(self, sid):
        with database.SessionLocal() as db:
            row = db.query(SpatialAnalysis).filter_by(session_id=sid, kind="agent").one()
            return row.status, row.reason, json.loads(row.tool_log or "{}"), json.loads(row.output_text or "null")

    def test_the_agent_updates_one_contact_once_and_the_phone_says_what_is_true(self):
        script = {"step": 0}

        def chat(messages, tools, tool_choice, timeout):
            incident = int(re.search(r"Fall alert (\d+)", messages[1]["content"]).group(1))
            tool_outputs = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            ids = [i["evidence_id"] for out in tool_outputs for i in out.get("items", []) if "evidence_id" in i]
            script["step"] += 1
            if script["step"] == 1:
                time.sleep(0.4)  # the stand-in notifier answers at once; give the delivery record time
                return {"message": {"content": "", "tool_calls": [call(1, "get_event_context", incident_id=incident),
                                                                  call(2, "get_delivery_status", incident_id=incident)]}, "usage": {}}
            if script["step"] == 2:
                delivery = next(i for i in ids if i.startswith("delivery:"))
                return {"message": {"content": "", "tool_calls": [
                    call(4, "cancel_alert", incident_id=incident),
                    call(7, "send_message", contact="contact_1", evidence_ids=[delivery, f"imu:{incident}"]),
                    call(8, "send_message", contact="contact_1", evidence_ids=[delivery]),
                    call(9, "reassure_wearer", template_id="alert_sent"),
                    call(10, "reassure_wearer", template_id="caregiver_ack")]}, "usage": {}}
            return {"message": {"content": json.dumps({
                "summary": "A fall alert was sent to the first contact.",
                "observations": [{"text": "The alert to contact_1 was sent.", "evidence_ids": [ids[-1]]}],
                "uncertainties": ["The data cannot show why the person fell."]})}, "usage": {}}
        spatial_runs._chat = chat
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                snap = self.wait(client, sid, lambda s: any(a["kind"] == "agent_update" for a in s["actions"])
                                 and any(u["template_id"] == "alert_sent" for u in s["wearer"]["spoken"]))
                self.wait(client, sid, lambda s: self.agent_row(sid)[0] == "Complete")

                update = next(a for a in snap["actions"] if a["kind"] == "agent_update")
                self.assertEqual(update["target"], "contact_1")
                self.assertEqual(update["detail"]["text"],
                                 f"Evidence update for fall alert #{iid}. Wearer response: No clear response. "
                                 "Wearer channel: Unknown. Camera: Unavailable. Scene analysis: Unavailable. Location: Unknown.")
                self.assertEqual([(c, ack) for c, _, ack in self.notifier.sent],
                                 [("contact_1", f"ack:{iid}:{snap['actions'][0]['id']}"), ("contact_1", None)])
                said = [(u["template_id"], u["source"]) for u in snap["wearer"]["spoken"] if u["source"] == "agent"]
                self.assertEqual(said, [("alert_sent", "agent")])

                status, _, log, answer = self.agent_row(sid)
                refused = {(c["tool"], c["args"].get("contact") or c["args"].get("template_id")): c["refused"]
                           for c in log["calls"] if "refused" in c}
                self.assertIn("not a tool the agent has", refused[("cancel_alert", None)])
                self.assertIn("already has the agent's update", refused[("send_message", "contact_1")])
                self.assertIn("no caregiver has acknowledged", refused[("reassure_wearer", "caregiver_ack")])
                self.assertEqual(answer["summary"], "A fall alert was sent to the first contact.")
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})

    def test_a_failing_model_does_not_delay_or_change_the_alert(self):
        def chat(messages, tools, tool_choice, timeout):
            from app.services.muse_client import AgentError
            raise AgentError("Failed", "HTTP 503: model unavailable")
        spatial_runs._chat = chat
        with TestClient(app) as client:
            sid, iid = self.open_incident(client)
            try:
                snap = self.wait(client, sid, lambda s: any(a["kind"] == "notify" and a["detail"]["status"] == "sent" for a in s["actions"]))
                self.wait(client, sid, lambda s: self.agent_row(sid)[0] == "Failed")
                self.assertEqual(self.agent_row(sid)[1], "HTTP 503: model unavailable")
                self.assertEqual([a["kind"] for a in client.get(f"/api/spatial/sessions/{sid}").json()["actions"]
                                  if a["kind"] in ("agent_update", "escalate")], [])
                self.assertEqual(len(self.notifier.sent), 1)
            finally:
                client.post(f"/api/spatial/sessions/{sid}/stop")
                client.post(f"/api/spatial/incidents/{iid}/acknowledge", json={"actor_id": "cleanup"})


if __name__ == "__main__":
    unittest.main()


def test_the_tool_budget_is_eight_calls_and_a_ninth_is_refused_by_the_loop():
    from app.services.incident_agent import run_tool_loop
    from app.services.responder import ResponderTools, _validate

    tools = ResponderTools(lambda: facts(), lambda *a: {"refused": "x"}, lambda t: {"refused": "x"})
    replies = [{"message": {"content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {
        "name": "reassure_wearer", "arguments": json.dumps({"template_id": "alert_sent"})}} for i in range(10)]}, "usage": {}},
        {"message": {"content": json.dumps({"summary": "s", "observations": [{"text": "t", "evidence_ids": ["imu:7"]}]})}, "usage": {}}]
    tools.seen_ids = {"imu:7"}
    chat = lambda messages, specs, choice, timeout: replies.pop(0)
    result = run_tool_loop("system", "user", TOOLS, tools, _validate, chat, max_tool_calls=8, timeout_seconds=5)
    assert result.status == "Complete"
    assert [c.get("refused") for c in result.tool_log].count("x") == 8 and len(result.tool_log) == 10
