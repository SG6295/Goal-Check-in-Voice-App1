"""Run with: python -m unittest discover -s tests -v.

Database tests use an outer transaction that is always rolled back.
No credentials or access codes are copied into tests.
"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import DEFAULT_PERSONA
from app.config import load_config
from app.dashboard import IST, clean_persona, router, validate_goal
from app.db import get_db
from app.models import Goal, Milestone, User


def form_data():
    today = datetime.now(IST).date()
    form = dict(title="Finish a project", done_definition="Deliver the final document",
                deadline=(today + timedelta(days=5)).isoformat())
    for i in range(1, 4):
        form[f"milestone_title_{i}"] = f"Deliverable {i}"
        form[f"milestone_date_{i}"] = (today + timedelta(days=4-i)).isoformat()
    return form


class ValidationTests(unittest.TestCase):
    def test_boundaries(self):
        today = datetime.now(IST).date()
        valid = form_data()
        self.assertFalse(validate_goal(valid, today)[0])
        for field, value in [
            ("title", ""), ("title", "x"*121),
            ("done_definition", ""), ("done_definition", "x"*501),
            ("deadline", ""), ("deadline", (today-timedelta(days=1)).isoformat()),
            ("milestone_title_1", "x"*81), ("milestone_date_1", "bad"),
            ("milestone_date_1", (today-timedelta(days=1)).isoformat()),
            ("milestone_date_1", (today+timedelta(days=6)).isoformat()),
            ("milestone_title_3", ""), ("milestone_title_5", "Partial row"),
        ]:
            with self.subTest(field=field, value_length=len(value)):
                self.assertTrue(validate_goal({**valid, field: value}, today)[0])
        for offset in [0, 5]:
            self.assertFalse(validate_goal({**valid, "milestone_date_1":
                (today+timedelta(days=offset)).isoformat()}, today)[0])
        self.assertFalse(validate_goal({**valid, "title": "x"*120,
            "done_definition": "x"*500, "milestone_title_1": "x"*80}, today)[0])

    def test_cleaning(self):
        self.assertEqual(clean_persona("  {{Warm}}\r\n\r\n\r\nDirect  "), "Warm\n\nDirect")
        self.assertEqual(clean_persona("{{}}"), "")
        self.assertNotIn("{{", clean_persona("{{{}}{"))


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(load_config().database_url)
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.db = Session(bind=self.connection, join_transaction_mode="create_savepoint")
        self.user = User(name="Stage two test", phone_e164=uuid4().hex[:16],
                         persona=DEFAULT_PERSONA, consent_at=datetime.now(IST),
                         consent_text_version="v1")
        self.other = User(name="Other test", phone_e164=uuid4().hex[:16],
                          persona=DEFAULT_PERSONA, consent_at=datetime.now(IST),
                          consent_text_version="v1")
        self.db.add_all([self.user, self.other])
        self.db.flush()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app, follow_redirects=False)
        self.identity = patch("app.dashboard.current_user", return_value=self.user)
        self.identity.start()

    def tearDown(self):
        self.identity.stop()
        self.client.close()
        self.db.close()
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def test_goal_and_milestones(self):
        self.assertIn("Set your goal", self.client.get("/").text)
        invalid = {**form_data(), "milestone_title_3": ""}
        self.assertEqual(self.client.post("/goal", data=invalid).status_code, 400)
        self.assertIsNone(self.db.scalar(select(Goal).where(Goal.user_id == self.user.id)))
        self.assertEqual(self.client.post("/goal", data=form_data()).status_code, 303)
        goal = self.db.scalar(select(Goal).where(Goal.user_id == self.user.id))
        rows = self.db.scalars(select(Milestone).join(Goal)
                              .where(Goal.user_id == self.user.id)
                              .order_by(Milestone.position)).all()
        self.assertEqual([m.position for m in rows], [1, 2, 3])
        self.assertEqual(self.client.post("/goal", data=form_data()).status_code, 409)
        page = self.client.get("/").text
        self.assertLess(page.index("Deliverable 3"), page.index("Deliverable 1"))
        self.assertIn("Check-in persona", page)
        self.assertEqual(self.client.post(f"/milestones/{rows[0].id}/done").status_code, 303)
        self.db.refresh(rows[0])
        self.assertEqual((rows[0].status, rows[0].status_source), ("done", "user"))
        self.assertIsNotNone(rows[0].status_updated_at)
        for status in ["done", "on_track", "at_risk", "blocked", "not_started", "unclear"]:
            rows[0].status = status
            rows[0].status_source = "corrected"
            self.db.flush()
            page = self.client.get("/").text
            self.assertIn(f"status-{status}", page)
            self.assertIn("corrected", page)
            self.assertIn("IST", page)
        with patch("app.dashboard.current_user", return_value=self.other):
            self.assertEqual(self.client.post(f"/milestones/{rows[0].id}/done").status_code, 404)
            self.assertNotIn(goal.title, self.client.get("/").text)
        self.assertEqual(self.client.post("/milestones/2147483647/done").status_code, 404)

    def test_integrity_rollback(self):
        with patch.object(self.db, "commit", side_effect=IntegrityError("test", {}, Exception())):
            self.assertEqual(self.client.post("/goal", data=form_data()).status_code, 409)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Goal)
                         .where(Goal.user_id == self.user.id)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Milestone)
                         .join(Goal).where(Goal.user_id == self.user.id)), 0)

    def test_persona(self):
        self.client.post("/goal", data=form_data())
        for value in ["", "  {{}}  ", "x"*501]:
            self.assertEqual(self.client.post("/persona", data={"persona": value}).status_code, 400)
        for value in ["x"*500, "  {{Warm}}\n\n\nDirect  "]:
            self.assertEqual(self.client.post("/persona", data={"persona": value}).status_code, 303)
            self.db.refresh(self.user)
            self.assertEqual(self.user.persona, clean_persona(value))
        self.assertEqual(self.client.post("/persona", data={"action": "reset"}).status_code, 303)
        self.db.refresh(self.user)
        self.assertEqual(self.user.persona, DEFAULT_PERSONA)


if __name__ == "__main__":
    unittest.main()
