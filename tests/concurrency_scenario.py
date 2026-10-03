"""Real committed SQLite transactions in separate threads, outside TestCase.

Launched by test_concurrency.py against a disposable, migrated database.
"""
import io
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone
from desk.importing import HEADER, import_episodes
from desk.models import Assignment, DatasetRequest, Episode, Robot, User
from desk.services import DomainError, assign, create_request, transition, unassign

call_command("migrate", verbosity=0)
owner = User.objects.create_user(username="client", email="client@example.com", role="client")
operator = User.objects.create_user(username="ops", email="ops@example.com", role="operator")
Robot.objects.create(pk="arm-01")


def new_request():
    item = create_request(owner, {"task_name": "pick cup", "episodes_requested": 1,
        "deadline": (timezone.localdate() + timedelta(days=1)).isoformat()})
    transition(operator, item.pk, "in_progress")
    return item


def race(*actions):
    barrier = Barrier(len(actions))

    def run(action):
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            return "ok", action()
        except DomainError as exc:
            return "conflict", exc.status
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(actions)) as executor:
        return list(executor.map(run, actions))


first, second = new_request(), new_request()
episode = Episode.objects.create(id="EP-1", robot_id="arm-01", task_name="pick cup",
    recorded_at=timezone.now(), duration_seconds=10, operator_name="Test", quality="good")
results = race(lambda: assign(operator, first.pk, episode.pk), lambda: assign(operator, second.pk, episode.pk))
assert sorted(result[0] for result in results) == ["conflict", "ok"], results
assert Assignment.objects.filter(episode=episode).count() == 1
winner = DatasetRequest.objects.get(pk=Assignment.objects.get(episode=episode).request_id)

# Delivery and removal must never produce an underfilled delivered request.
results = race(lambda: transition(operator, winner.pk, "delivered"), lambda: unassign(operator, winner.pk, episode.pk))
assert sorted(result[0] for result in results) == ["conflict", "ok"], results
winner.refresh_from_db()
if winner.status == "in_progress":
    assert winner.assignments.count() == 0
    assign(operator, winner.pk, episode.pk)
else:
    assert winner.status == "delivered" and winner.assignments.count() == 1
    transition(owner, winner.pk, "rejected", feedback="Please make a change.")
    transition(operator, winner.pk, "in_progress")

before = winner.events.filter(to_status="delivered").count()
results = race(lambda: transition(operator, winner.pk, "delivered"), lambda: transition(operator, winner.pk, "delivered"))
assert sorted(result[0] for result in results) == ["conflict", "ok"], results
assert winner.events.filter(to_status="delivered").count() == before + 1

csv_text = ",".join(HEADER) + "\n" + "".join(
    f"EP-{i},arm-01,pick cup,2020-01-01T00:00:00,20,Test,good\n" for i in range(1000, 1600))
results = race(*(lambda: import_episodes(io.StringIO(csv_text), operator) for _ in range(2)))
assert all(result[0] == "ok" for result in results), results
assert sum(result[1]["imported"] for result in results) == 600
assert Episode.objects.count() == 601
print("PASS: concurrent assignment, delivery/removal, duplicate delivery, duplicate import")
