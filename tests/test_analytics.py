from datetime import datetime, timedelta, timezone
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from desk.analytics import analytics
from desk.models import DatasetRequest, StatusEvent
from desk.services import assign, transition

pytestmark = pytest.mark.django_db
START = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("hours,expected", [([], None), ([2], 7200), ([1, 3], 7200), ([1, 3, 8], 10800)])
def test_sql_median_first_delivery_and_submission_cohort(users, make_request, make_episode, hours, expected):
    for index, duration in enumerate(hours):
        item = make_request()
        DatasetRequest.objects.filter(pk=item.pk).update(created_at=START)
        transition(users["ops"], item.pk, "in_progress")
        assign(users["ops"], item.pk, make_episode().pk)
        transition(users["ops"], item.pk, "delivered")
        StatusEvent.objects.filter(request=item, to_status="delivered").update(created_at=START + timedelta(hours=duration))
        if index == 0:
            transition(users["client"], item.pk, "rejected", feedback="Please revise the delivery.")
            transition(users["ops"], item.pk, "in_progress")
            transition(users["ops"], item.pk, "delivered")
    with CaptureQueriesContext(connection) as queries:
        result = analytics(users["ops"], {"start": "2026-08-01", "end": "2026-08-01"})
    assert result["median_seconds_to_first_delivery"] == (None if expected is None else pytest.approx(expected, abs=.001))
    assert result["requests_by_status"]["delivered"] == len(hours)
    assert len(queries) == 4
    assert any("ROW_NUMBER() OVER" in q["sql"] for q in queries)


def test_episode_range_grouping_good_only_and_deterministic_top_five(users, make_episode):
    for i in range(7):
        make_episode(recorded_at=START, task_name=f"task {i}")
    make_episode(recorded_at=START + timedelta(hours=23, minutes=59), task_name="task 1")
    make_episode(recorded_at=START, quality="bad", task_name="ignored")
    make_episode(recorded_at=START + timedelta(days=1), task_name="outside")
    result = analytics(users["ops"], {"start": "2026-08-01", "end": "2026-08-01"})
    assert result["episodes_per_day_robot"][0]["count"] == 9
    assert len(result["top_good_tasks"]) == 5
    assert result["top_good_tasks"][0] == {"task_name": "task 1", "count": 2}
    assert result["top_good_tasks"][1]["task_name"] == "task 0"


@pytest.mark.parametrize("query", ["", "?start=bad&end=2026-01-01", "?start=2026-02-01&end=2026-01-01", "?start=2020-01-01&end=2026-01-01", "?start=9999-12-31&end=9999-12-31"])
def test_invalid_analytics_range(client, users, query):
    client.force_login(users["ops"])
    assert client.get("/api/analytics/" + query).status_code == 400
