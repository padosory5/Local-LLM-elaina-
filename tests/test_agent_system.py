import json
import tempfile
import unittest
from pathlib import Path

from agents.builder import AgentBuilder
from agents.calendar_agent import GoogleCalendarAgent
from agents.registry import AgentRegistry
from agents.task_manager import AgentTaskManager
from security.approval_manager import ApprovalManager
from security.policy import PolicyEngine
from tools.google_calendar import GoogleCalendarTool


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def chat(self, **_kwargs):
        payload = self.payloads.pop(0)
        return {
            "message": {
                "content": json.dumps(payload),
            },
        }


class AgentSystemTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_existing_intents_resolve_to_specialist_agents(self):
        registry = AgentRegistry(
            user_directory=self.root / "agents",
        )

        self.assertEqual(
            registry.for_intent("conversation").id,
            "conversation_agent",
        )
        self.assertEqual(
            registry.for_intent("web_search").id,
            "research_agent",
        )
        self.assertEqual(
            registry.for_intent("screen_analysis").id,
            "vision_agent",
        )
        self.assertEqual(
            registry.for_intent("project_edit").id,
            "coding_agent",
        )
        self.assertEqual(
            registry.for_intent("git_publish").id,
            "git_agent",
        )

    def test_calendar_builder_collects_settings_before_definition(self):
        builder = AgentBuilder(
            client=FakeClient([
                {
                    "capability": "google_calendar",
                    "cancel_requested": False,
                    "timezone": "",
                    "calendar_id": "",
                    "default_duration_minutes": None,
                    "approval_confirmed": False,
                },
                {
                    "capability": "google_calendar",
                    "cancel_requested": False,
                    "timezone": "Asia/Seoul",
                    "calendar_id": "primary",
                    "default_duration_minutes": 60,
                    "approval_confirmed": True,
                },
            ]),
            model="test",
            keep_alive=0,
        )

        first = builder.handle(
            "Create an agent that writes events to my Google Calendar."
        )
        self.assertEqual(first.status, "input_required")
        self.assertTrue(builder.active)

        second = builder.handle(
            "Use Asia/Seoul, primary, 60 minutes, and ask every time."
        )
        self.assertEqual(second.status, "ready")
        self.assertEqual(second.definition["id"], "google_calendar_agent")
        self.assertFalse(builder.active)

    def test_calendar_builder_understands_semantic_cancellation(self):
        builder = AgentBuilder(
            client=FakeClient([
                {
                    "capability": "google_calendar",
                    "cancel_requested": False,
                    "timezone": "",
                    "calendar_id": "",
                    "default_duration_minutes": None,
                    "approval_confirmed": False,
                },
                {
                    "capability": "google_calendar",
                    "cancel_requested": True,
                    "timezone": "",
                    "calendar_id": "",
                    "default_duration_minutes": None,
                    "approval_confirmed": False,
                },
            ]),
            model="test",
            keep_alive=0,
        )

        builder.handle("Create a calendar-management agent.")
        result = builder.handle(
            "Actually, scrap that setup—I changed my mind."
        )

        self.assertEqual(result.status, "cancelled")
        self.assertFalse(builder.active)

    def test_calendar_agent_prepares_but_does_not_write_event(self):
        registry = AgentRegistry(
            user_directory=self.root / "agents",
        )
        blueprint = {
            "id": "google_calendar_agent",
            "name": "Google Calendar Agent",
            "description": "Creates approved events.",
            "enabled": True,
            "intents": ["calendar_action"],
            "tools": ["calendar.create_event"],
            "instructions": ["Always ask for approval."],
            "settings": {
                "timezone": "Asia/Seoul",
                "calendar_id": "primary",
                "default_duration_minutes": 60,
                "approval_required": True,
            },
        }
        definition = registry.install_user_agent(blueprint)
        calendar = GoogleCalendarAgent(
            client=FakeClient([{
                "summary": "Math class",
                "start": "2026-08-03T10:00:00+09:00",
                "end": "2026-08-03T11:30:00+09:00",
                "description": "",
                "location": "Engineering Hall",
            }]),
            model="test",
            keep_alive=0,
        )

        result = calendar.handle(
            "Add math class on August 3 at 10 for 90 minutes.",
            definition,
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.event["summary"], "Math class")
        self.assertEqual(result.calendar_id, "primary")

    def test_approval_payload_is_not_reusable(self):
        manager = ApprovalManager(
            PolicyEngine(),
            audit_path=self.root / "approvals.jsonl",
        )
        proposal = manager.create(
            action="calendar.create_event",
            title="Create event",
            summary="Review event.",
            details=[{"label": "Title", "value": "Math"}],
            payload={"event": {"summary": "Math"}},
        )

        resolved = manager.resolve(proposal.proposal_id, True)
        self.assertEqual(resolved.status, "approved")
        with self.assertRaises(ValueError):
            manager.resolve(proposal.proposal_id, True)

    def test_agent_tasks_have_explicit_states(self):
        manager = AgentTaskManager(
            audit_path=self.root / "tasks.jsonl",
        )
        task = manager.start("research_agent", "Find current information.")
        manager.update(task.id, "completed", "Search finished.")

        self.assertEqual(manager.get(task.id).status, "completed")
        self.assertTrue((self.root / "tasks.jsonl").is_file())

    def test_write_policies_require_approval(self):
        policy = PolicyEngine()

        self.assertTrue(
            policy.requires_approval("agent.install")
        )
        self.assertTrue(
            policy.requires_approval("calendar.create_event")
        )
        self.assertFalse(
            policy.requires_approval("web.search")
        )

    def test_calendar_tool_submits_exact_validated_payload_once(self):
        class FakeInsert:
            def __init__(self):
                self.execute_calls = 0

            def execute(self):
                self.execute_calls += 1
                return {
                    "id": "event-1",
                    "summary": "Math",
                    "start": {"dateTime": "2026-08-03T10:00:00+09:00"},
                    "end": {"dateTime": "2026-08-03T11:00:00+09:00"},
                }

        class FakeEvents:
            def __init__(self, insertion):
                self.insertion = insertion
                self.calls = []

            def insert(self, **kwargs):
                self.calls.append(kwargs)
                return self.insertion

        class FakeService:
            def __init__(self, events):
                self.fake_events = events

            def events(self):
                return self.fake_events

        event = {
            "summary": "Math",
            "start": {
                "dateTime": "2026-08-03T10:00:00+09:00",
                "timeZone": "Asia/Seoul",
            },
            "end": {
                "dateTime": "2026-08-03T11:00:00+09:00",
                "timeZone": "Asia/Seoul",
            },
        }
        insertion = FakeInsert()
        events = FakeEvents(insertion)

        tool = object.__new__(GoogleCalendarTool)
        tool.readiness = lambda: (True, "ready")
        tool._build_service = lambda: FakeService(events)

        result = tool.create_event(
            calendar_id="primary",
            event=event,
        )

        self.assertEqual(result["event_id"], "event-1")
        self.assertEqual(len(events.calls), 1)
        self.assertEqual(events.calls[0]["body"], event)
        self.assertEqual(insertion.execute_calls, 1)


if __name__ == "__main__":
    unittest.main()
