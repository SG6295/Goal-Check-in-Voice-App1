"""Stage 3 tests never contact the provider or dial a phone."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select

import test_stage2
from test_stage2 import form_data
from app.calls import initiate, availability, now_ist, dynamic_variables, detail, history
from app.config import load_config
from app.call_processing import normalize_status, process_event
from app.models import Call, Goal, Milestone, WebhookEvent


class CallTests(unittest.TestCase):
    setUp = test_stage2.RouteTests.setUp
    tearDown = test_stage2.RouteTests.tearDown

    def test_webhook_retry_and_duplicates(self):
        from app.webhooks import router
        self.client.app.include_router(router)
        event = {"type": "post_call_transcription", "event_timestamp": 123,
                 "data": {"conversation_id": uuid4().hex}}
        with patch("app.webhooks.ElevenLabs") as sdk:
            sdk.return_value.webhooks.construct_event.return_value = event
            with patch("app.webhooks.process_event", side_effect=RuntimeError("test rollback")):
                self.assertEqual(self.client.post("/webhooks/elevenlabs", content="{}").status_code, 500)
            self.assertIsNone(self.db.scalar(select(WebhookEvent).where(
                WebhookEvent.conversation_id == event["data"]["conversation_id"])))
            response = self.client.post("/webhooks/elevenlabs", content="{}")
            self.assertEqual(response.json(), {"status": "received", "duplicate": False})
            record = self.db.scalar(select(WebhookEvent).where(
                WebhookEvent.conversation_id == event["data"]["conversation_id"]))
            self.assertIsNotNone(record.processed_at)
            with patch("app.webhooks.process_event") as processor:
                self.assertTrue(self.client.post("/webhooks/elevenlabs", content="{}").json()["duplicate"])
                processor.assert_not_called()
            sdk.return_value.webhooks.construct_event.return_value = {"type": "post_call_audio"}
            self.assertEqual(self.client.post("/webhooks/elevenlabs", content="{}").status_code, 200)
            sdk.return_value.webhooks.construct_event.side_effect = ValueError()
            self.assertEqual(self.client.post("/webhooks/elevenlabs", content="{}").status_code, 401)

    def prepare(self):
        self.client.post("/goal", data=form_data())
        self.goal = self.db.scalar(select(Goal).where(Goal.user_id == self.user.id))
        self.request = Request({"type": "http", "method": "POST", "path": "/",
                                "headers": [], "session": {}})
        self.config = replace(load_config(), call_mode="sip")
        self.now = now_ist().replace(hour=12, minute=0, second=0, microsecond=0)
        self.patches = [
            patch("app.calls.current_user", return_value=self.user),
            patch("app.calls.load_config", return_value=self.config),
            patch("app.calls.now_ist", return_value=self.now),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def start_call(self, error=None):
        with patch("app.calls.place_call", return_value=(
            {"success": True, "conversation_id": uuid4().hex, "sip_call_id": "test-sip"}, error
        )) as provider:
            response = initiate(self.goal.id, self.request, self.db)
        return response, provider

    def test_success_lock_and_failure_exclusion(self):
        self.prepare()
        response, provider = self.start_call("api_error: 503 unavailable")
        self.assertEqual(response.status_code, 303)
        self.db.refresh(self.goal)
        self.assertIsNone(self.goal.call_lock_until)
        self.assertEqual(availability(self.db, self.user, self.goal)["used"], 0)
        response, provider = self.start_call()
        self.assertEqual(provider.call_count, 1)
        variables = provider.call_args.args[2]
        self.assertTrue(all(isinstance(value, str) for value in variables.values()))
        self.assertEqual(self.request.session["flash"], "Calling you now")
        self.assertEqual(availability(self.db, self.user, self.goal)["used"], 1)
        _, provider = self.start_call()
        self.assertEqual(provider.call_count, 0)
        self.assertIn("already in progress", self.request.session["flash"])

    def test_guards(self):
        self.prepare()
        with self.assertRaises(HTTPException) as error:
            initiate(2147483647, self.request, self.db)
        self.assertEqual(error.exception.status_code, 404)
        for hour in [8, 21]:
            with patch("app.calls.now_ist", return_value=self.now.replace(hour=hour)):
                _, provider = self.start_call()
                self.assertEqual(provider.call_count, 0)
        self.user.opted_out_at = self.now
        self.db.flush()
        _, provider = self.start_call()
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(self.request.session["flash"], "You opted out of calls")
        self.user.opted_out_at = None
        self.db.flush()
        with patch("app.calls.load_config", return_value=replace(self.config, call_mode="web")):
            _, provider = self.start_call()
            self.assertEqual(provider.call_count, 0)
            self.assertEqual(self.request.session["flash"], "Phone calling is not enabled")

    def test_caps_and_stale_sweep(self):
        self.prepare()
        for _ in range(self.config.max_calls_per_goal_per_day):
            self.db.add(Call(id=uuid4(), user_id=self.user.id, goal_id=self.goal.id,
                             requested_at=self.now, state="completed"))
        self.db.commit()
        _, provider = self.start_call()
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(self.request.session["flash"], "Daily call limit reached")
        calls = self.db.scalars(select(Call).where(Call.user_id == self.user.id)).all()
        for call in calls:
            call.failure_reason = "api_error: test"
        stale = calls[0]
        stale.state = "in_progress"
        stale.requested_at = self.now-timedelta(minutes=self.config.call_lock_minutes+1)
        self.goal.call_lock_until = self.now+timedelta(minutes=5)
        self.db.commit()
        _, provider = self.start_call()
        self.assertEqual(provider.call_count, 1)
        self.db.refresh(stale)
        self.assertEqual((stale.state, stale.failure_reason), ("failed", "no_webhook"))
        with patch("app.calls.load_config", return_value=replace(self.config, max_concurrent_calls=1)):
            _, provider = self.start_call()
            self.assertEqual(provider.call_count, 0)
            self.assertEqual(self.request.session["flash"], "All lines are busy, try again in a minute")

    def test_transcription_and_history_ownership(self):
        self.prepare()
        self.start_call()
        call = self.db.scalar(select(Call).where(Call.user_id == self.user.id))
        record = WebhookEvent(conversation_id=call.conversation_id, event_type="post_call_transcription")
        self.db.add(record)
        event = {"type": "post_call_transcription", "data": {
            "conversation_initiation_client_data": {"dynamic_variables": {"call_id": str(call.id)}},
            "transcript": [{"role": "agent", "message": "<script>test</script>", "time_in_call_secs": 65}],
            "metadata": {"call_duration_secs": 40},
            "analysis": {"transcript_summary": "Summary", "data_collection_results": {
                "m1_status": {"value": "Completed"}, "m2_status": "at risk",
                "blocker": {"value": "none"}, "opt_out": {"value": "true"}
            }}}}
        process_event(self.db, event, record)
        self.db.flush()
        self.assertEqual((call.state, call.m1_status, call.m2_status), ("completed", "done", "at_risk"))
        self.assertEqual(call.m1_status_raw, "Completed")
        self.assertIsNone(call.blocker)
        self.db.refresh(self.user)
        self.assertIsNotNone(self.user.opted_out_at)
        self.db.refresh(self.goal)
        self.assertIsNone(self.goal.call_lock_until)
        m = self.db.scalar(select(Milestone).join(Goal).where(
            Milestone.id == call.milestone_1_id, Goal.user_id == self.user.id))
        self.assertEqual((m.status, m.status_source), ("done", "agent"))
        response = detail(call.id, self.request, self.db)
        self.assertIn(b"1:05", response.body)
        self.assertIn(b"&lt;script&gt;", response.body)
        with patch("app.calls.current_user", return_value=self.other):
            with self.assertRaises(HTTPException) as error:
                detail(call.id, self.request, self.db)
            self.assertEqual(error.exception.status_code, 404)
            self.assertNotIn(str(call.id).encode(), history(self.request, self.db).body)

    def test_short_review_and_failure_states(self):
        self.prepare()
        self.start_call()
        call = self.db.scalar(select(Call).where(Call.user_id == self.user.id))
        for event_type, data, expected in [
            ("post_call_transcription", {"metadata": {"call_duration_secs": 10}}, "no_review"),
            ("call_initiation_failure", {"failure_reason": "busy"}, "busy"),
            ("call_initiation_failure", {"failure_reason": "no-answer"}, "no_answer"),
            ("call_initiation_failure", {"failure_reason": "other", "metadata": {
                "body": {"sip_status_code": 500, "error_reason": "test failure"}}}, "failed"),
        ]:
            record = WebhookEvent()
            process_event(self.db, {"type": event_type, "data": {
                **data, "conversation_id": call.conversation_id}}, record)
            self.assertEqual(call.state, expected)
            self.assertIsNotNone(record.processed_at)
        self.assertIn("500 test failure", call.failure_reason)


class NormalizationTests(unittest.TestCase):
    def test_statuses(self):
        for raw, expected in [(None, None), ("complete", "done"), ("finished", "done"),
                              ("ON TRACK", "on_track"), ("at-risk", "at_risk"),
                              ("behind", "at_risk"), ("delayed", "at_risk"),
                              ("blocked", "blocked"), ("not applicable", "not_applicable"),
                              ("anything else", "unclear")]:
            self.assertEqual(normalize_status(raw), expected)
