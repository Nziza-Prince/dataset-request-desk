import json
import logging
from datetime import timedelta
import pytest
from django.test import Client
from django.utils import timezone
from desk.models import User
from desk.services import assign, transition

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("url", ["/health", "/api/me/", "/api/requests/", "/api/requests/1/", "/api/requests/1/events/", "/api/requests/1/assignments/", "/api/episodes/", "/api/analytics/", "/api/users/"])
def test_auth_required(client, url):
    assert client.get(url).status_code == 401


def test_ownership_read_write_and_no_mass_assignment(client, users, make_request, make_episode):
    own, other = make_request(), make_request("other")
    client.force_login(users["client"])
    assert [r["id"] for r in client.get("/api/requests/").json()["results"]] == [own.pk]
    for suffix in ["", "events/", "assignments/"]:
        assert client.get(f"/api/requests/{other.pk}/{suffix}").status_code == 404
    assert client.get(f"/requests/{other.pk}/").status_code == 404
    assert client.post(f"/api/requests/{other.pk}/transition/", {"status": "accepted"}, content_type="application/json").status_code == 404
    assert client.post(f"/api/requests/{own.pk}/assignments/", {"episode_id": make_episode().pk}, content_type="application/json").status_code == 403
    assert client.get("/api/episodes/").status_code == 403
    assert client.get("/api/analytics/?start=2026-01-01&end=2026-10-03").status_code == 403
    assert client.post("/api/requests/", {"client_id": users["other"].pk}, content_type="application/json").status_code == 400


def test_creation_validation_and_roles(client, users):
    data = {"task_name": " Pick  CUP ", "episodes_requested": 2,
            "deadline": (timezone.localdate() + timedelta(days=1)).isoformat()}
    client.force_login(users["ops"])
    assert client.post("/api/requests/", data, content_type="application/json").status_code == 403
    client.force_login(users["client"])
    for count in [0, -1, 1.2, True, "2", 1000001]:
        assert client.post("/api/requests/", {**data, "episodes_requested": count}, content_type="application/json").status_code == 400
    assert client.post("/api/requests/", {**data, "deadline": "2000-01-01"}, content_type="application/json").status_code == 400
    response = client.post("/api/requests/", data, content_type="application/json")
    assert response.status_code == 201
    assert response.json()["task_name"] == "pick cup"
    assert response.json()["client_id"] == users["client"].pk


def test_client_selects_a_known_action_and_cannot_forge_an_unknown_one(client, users, make_episode):
    make_episode(task_name="wipe table")
    make_episode(task_name="unusable task", quality="bad")
    client.force_login(users["client"])

    page = client.get("/")
    assert b'<select name="task_name"' in page.content
    assert b'<option value="wipe table">Wipe table</option>' in page.content
    assert b"unusable task" not in page.content
    assert b'type="number" name="episodes_requested"' in page.content
    for attribute in (b'min="1"', b'max="1000000"', b'step="1"', b'inputmode="numeric"'):
        assert attribute in page.content

    data = {
        "task_name": "invented action",
        "episodes_requested": 1,
        "deadline": (timezone.localdate() + timedelta(days=1)).isoformat(),
    }
    html_response = client.post("/", data)
    assert html_response.status_code == 200
    assert b"Choose one of the available actions." in html_response.content
    assert client.post("/api/requests/", data, content_type="application/json").status_code == 400

    data["task_name"] = "wipe table"
    assert client.post("/", data).status_code == 302


def test_csrf_login_logout_and_deactivation(users):
    browser = Client(enforce_csrf_checks=True)
    data = {"email": users["ops"].email, "password": "test-password-42"}
    assert browser.post("/api/login/", data, content_type="application/json").status_code == 403
    token = browser.get("/api/csrf/").json()["csrf_token"]
    response = browser.post("/api/login/", data, content_type="application/json", HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200
    assert response.cookies["sessionid"]["httponly"]
    assert browser.get("/api/me/").json()["role"] == "operator"
    assert browser.post("/api/logout/").status_code == 403
    token = response.json()["csrf_token"]
    assert browser.post("/api/logout/", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert browser.get("/api/me/").status_code == 401
    browser.force_login(users["ops"])
    User.objects.filter(pk=users["ops"].pk).update(is_active=False)
    assert browser.get("/api/me/").status_code == 401


def test_login_throttle_and_generic_errors(client, users):
    for _ in range(10):
        response = client.post("/api/login/", {"email": users["client"].email, "password": "wrong"}, content_type="application/json")
        assert response.status_code == 401 and response.json()["error"] == "Invalid credentials"
    assert client.post("/api/login/", {"email": users["client"].email, "password": "test-password-42"}, content_type="application/json").status_code == 429


def test_user_management_and_immediate_role_change(client, users):
    client.force_login(users["ops"])
    assert client.get("/api/users/").status_code == 403
    client.force_login(users["admin"])
    data = {"email": "NEW@example.com", "name": "New User", "password": "long-random-test-42", "role": "operator"}
    response = client.post("/api/users/", data, content_type="application/json")
    assert response.status_code == 201
    new = User.objects.get(pk=response.json()["id"])
    assert new.email == "new@example.com" and new.check_password(data["password"])
    assert new.password != data["password"]
    assert client.post("/api/users/", data, content_type="application/json").status_code == 409
    assert client.patch(f"/api/users/{new.pk}/", {"role": "client"}, content_type="application/json").status_code == 200
    assert client.patch(f"/api/users/{new.pk}/", {"is_active": "false"}, content_type="application/json").status_code == 400
    assert client.patch(f"/api/users/{new.pk}/", {"is_active": False}, content_type="application/json").status_code == 200
    client.force_login(users["ops"])
    User.objects.filter(pk=users["ops"].pk).update(role="client")
    assert client.get("/api/episodes/").status_code == 403


def test_malformed_input_and_methods(client, users):
    client.force_login(users["client"])
    for data in ["{", "[]", '{"task_name": {}}', '{"task_name": null}', '{"task_name": true}', '{"task_name": 123}']:
        assert client.post("/api/requests/", data, content_type="application/json").status_code == 400
    assert client.post("/api/requests/", {}).status_code == 415
    assert client.delete("/api/requests/").status_code == 405
    assert client.get("/api/requests/?after=invalid").status_code == 400
    assert client.get("/api/requests/9999999999999999999999999999/").status_code == 404


def test_import_size_limit_and_database_busy(client, users, settings):
    from unittest.mock import patch
    from django.db import OperationalError
    client.force_login(users["ops"])
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 80
    assert client.post("/api/episodes/import/", b"x" * 81, content_type="text/csv").status_code == 413
    with patch("desk.api.available_episodes", side_effect=OperationalError("database is locked")):
        response = client.get("/api/episodes/")
    assert response.status_code == 503 and response["Retry-After"] == "2"


def test_html_flow_and_escaping(client, users, make_episode):
    assert client.get("/").status_code == 302
    assert client.get("/login/").status_code == 200
    client.force_login(users["client"])
    response = client.post("/", {"task_name": "pick cup", "episodes_requested": 1,
        "deadline": (timezone.localdate() + timedelta(days=2)).isoformat(), "notes": "<script>alert(1)</script>"})
    assert response.status_code == 302
    url = response.url
    assert b"&lt;script&gt;" in client.get(url).content
    client.force_login(users["ops"])
    client.post(url, {"action": "transition", "status": "in_progress"})
    ep = make_episode()
    assert b"Choose matching clips" in client.get(url).content
    client.post(url, {"action": "assign", "episode_id": ep.pk})
    client.post(url, {"action": "transition", "status": "delivered"})
    client.force_login(users["client"])
    assert b"Accept delivery" in client.get(url).content
    client.post(url, {"action": "transition", "status": "accepted"})
    page = client.get(url).content
    assert b"Accepted" in page
    assert b"Delivery accepted" in page
    assert b"approved the delivered clips" in page


def test_html_rejection_feedback_and_operator_tools(client, users, make_request, make_episode):
    item = make_request()
    url = f"/requests/{item.pk}/"
    client.force_login(users["ops"])
    client.post(url, {"action": "transition", "status": "in_progress"})
    client.post(url, {"action": "assign", "episode_id": make_episode().pk})
    client.post(url, {"action": "transition", "status": "delivered"})
    client.force_login(users["client"])
    assert b"What needs to change?" in client.get(url).content
    client.post(url, {"action": "transition", "status": "rejected", "feedback": "Use a wider camera angle."})
    client.force_login(users["ops"])
    page = client.get(url).content
    assert b"Use a wider camera angle." in page
    assert client.get("/imports/").status_code == 200
    assert client.get("/analytics/").status_code == 200


def test_feasibility_review_explains_a_large_request(client, users, make_request, make_episode):
    item = make_request(episodes_requested=1_000_000)
    make_episode()
    client.force_login(users["client"])
    home = client.get("/").content
    assert b"Needs feasibility review" in home
    assert b"999999 clips short" in home
    client.force_login(users["ops"])
    detail = client.get(f"/requests/{item.pk}/").content
    assert b"Only 1 matching good or usable clips" in detail
    assert b"shortfall of 999999" in detail


def test_feasibility_is_not_flagged_when_matching_inventory_is_sufficient(client, users, make_request, make_episode):
    item = make_request(episodes_requested=2)
    make_episode()
    make_episode()
    client.force_login(users["ops"])
    assert b"Needs feasibility review" not in client.get(f"/requests/{item.pk}/").content


def test_analytics_uses_calendar_date_inputs(client, users):
    client.force_login(users["ops"])

    page = client.get("/analytics/")

    assert page.status_code == 200
    assert b'type="date" name="start"' in page.content
    assert b'type="date" name="end"' in page.content

    invalid = client.get("/analytics/?start=not-a-date&end=also-not-a-date")
    assert invalid.status_code == 200
    assert invalid.content.count(b"Enter a valid date.") == 2


def test_request_deadline_calendar_disallows_past_dates_in_browser(client, users):
    client.force_login(users["client"])

    page = client.get("/")

    expected = f'type="date" name="deadline" min="{timezone.localdate().isoformat()}"'.encode()
    assert expected in page.content


def test_every_request_status_renders_for_client_and_operator(client, users, make_request, make_episode):
    items = [make_request()]

    in_progress = make_request()
    transition(users["ops"], in_progress.pk, "in_progress")
    items.append(in_progress)

    delivered = make_request()
    transition(users["ops"], delivered.pk, "in_progress")
    assign(users["ops"], delivered.pk, make_episode().pk)
    transition(users["ops"], delivered.pk, "delivered")
    items.append(delivered)

    accepted = make_request()
    transition(users["ops"], accepted.pk, "in_progress")
    assign(users["ops"], accepted.pk, make_episode().pk)
    transition(users["ops"], accepted.pk, "delivered")
    transition(users["client"], accepted.pk, "accepted")
    items.append(accepted)

    rejected = make_request()
    transition(users["ops"], rejected.pk, "in_progress")
    assign(users["ops"], rejected.pk, make_episode().pk)
    transition(users["ops"], rejected.pk, "delivered")
    transition(users["client"], rejected.pk, "rejected", feedback="Please use another angle.")
    items.append(rejected)

    cancelled = make_request()
    transition(users["client"], cancelled.pk, "cancelled", feedback="No longer needed.")
    items.append(cancelled)

    for actor in (users["client"], users["ops"]):
        client.force_login(actor)
        assert client.get("/").status_code == 200
        for item in items:
            assert client.get(f"/requests/{item.pk}/").status_code == 200


def test_html_cancellation_shows_a_reason_and_pre_filled_replacement(client, users, make_request, make_episode):
    make_episode(task_name="pick cup")
    item = make_request(episodes_requested=200, deadline=(timezone.localdate() + timedelta(days=7)).isoformat())
    url = f"/requests/{item.pk}/"
    transition(users["ops"], item.pk, "in_progress")
    client.force_login(users["client"])
    page = client.get(url).content
    assert b"Why are you cancelling?" in page
    response = client.post(url, {"action": "transition", "status": "cancelled", "feedback": "Need a smaller order."})
    assert response.status_code == 302
    page = client.get(url).content
    assert b"Cancellation reason" in page
    assert b"Need a smaller order." in page
    assert b"Create revised request" in page
    assert b"Request cancelled" in client.get("/").content
    replacement = client.get(f"/?new=1&task_name=pick%20cup&episodes_requested=200&deadline={item.deadline.isoformat()}")
    assert b'<option value="pick cup" selected>Pick cup</option>' in replacement.content
    assert b'value="200"' in replacement.content


def test_transition_api_requires_rejection_feedback(client, users, make_request, make_episode):
    item = make_request()
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, make_episode().pk)
    transition(users["ops"], item.pk, "delivered")
    client.force_login(users["client"])
    response = client.post(f"/api/requests/{item.pk}/transition/", {"status": "rejected"}, content_type="application/json")
    assert response.status_code == 400
    response = client.post(f"/api/requests/{item.pk}/transition/", {"status": "rejected", "feedback": "Missing an angle."}, content_type="application/json")
    assert response.status_code == 200
    assert client.get(f"/api/requests/{item.pk}/events/").json()["results"][-1]["feedback"] == "Missing an angle."


def test_health_and_request_logs(client, users, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("desk.requests"), "propagate", True)
    client.force_login(users["ops"])
    with caplog.at_level("INFO", logger="desk.requests"):
        response = client.get("/health")
    assert response.json() == {"status": "ok"}
    entries = [json.loads(record.message) for record in caplog.records if record.name == "desk.requests"]
    assert entries[-1]["user_id"] == users["ops"].pk
    assert entries[-1]["method"] == "GET" and entries[-1]["path"] == "/health"
    assert entries[-1]["status"] == 200 and entries[-1]["duration_ms"] >= 0


def test_paginated_api_and_import_permissions(client, users, make_episode):
    for _ in range(51):
        make_episode()
    client.force_login(users["ops"])
    first = client.get("/api/episodes/").json()
    assert len(first["results"]) == 50 and first["next_after"]
    second = client.get("/api/episodes/", {"after": first["next_after"]}).json()
    assert len(second["results"]) == 1 and second["next_after"] is None
    assert client.post("/api/episodes/import/", b"wrong", content_type="text/csv").status_code == 400
    client.force_login(users["client"])
    assert client.post("/api/episodes/import/", b"wrong", content_type="text/csv").status_code == 403
