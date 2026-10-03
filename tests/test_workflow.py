from unittest.mock import patch
import pytest
from django.db import IntegrityError, transaction
from desk.models import Assignment, DatasetRequest, StatusEvent, User
from desk.services import DomainError, assign, transition, unassign, update_user

pytestmark = pytest.mark.django_db


def test_full_rework_cycle_and_audit(users, make_request, make_episode):
    item, episode = make_request(), make_episode()
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, episode.pk)
    assert assign(users["ops2"], item.pk, episode.pk).episode_id == episode.pk
    assert item.assignments.count() == 1
    transition(users["ops"], item.pk, "delivered")
    for action in (lambda: unassign(users["ops"], item.pk, episode.pk),
                   lambda: assign(users["ops"], item.pk, episode.pk)):
        with pytest.raises(DomainError):
            action()
    transition(users["client"], item.pk, "rejected", feedback="Please use a different robot.")
    with pytest.raises(DomainError):
        unassign(users["ops"], item.pk, episode.pk)
    transition(users["admin"], item.pk, "in_progress")
    unassign(users["ops"], item.pk, episode.pk)
    replacement = make_episode()
    assign(users["ops"], item.pk, replacement.pk)
    transition(users["ops"], item.pk, "delivered")
    transition(users["client"], item.pk, "accepted")
    events = list(item.events.order_by("id"))
    assert [e.to_status for e in events] == ["submitted", "in_progress", "delivered", "rejected", "in_progress", "delivered", "accepted"]
    assert [e.actor_id for e in events] == [users[key].pk for key in ["client", "ops", "ops", "client", "admin", "ops", "client"]]
    assert all(e.created_at for e in events)
    with pytest.raises(DomainError):
        transition(users["ops"], item.pk, "in_progress")


@pytest.mark.parametrize("actor,target", [("client", "in_progress"), ("other", "in_progress"), ("ops", "accepted"), ("ops", "delivered")])
def test_invalid_transitions_do_not_write(users, make_request, actor, target):
    item = make_request()
    with pytest.raises(DomainError):
        transition(users[actor], item.pk, target)
    item.refresh_from_db()
    assert item.status == "submitted"
    assert item.events.count() == 1


@pytest.mark.parametrize("actor", ["ops", "admin", "other"])
def test_only_owner_can_review(users, make_request, make_episode, actor):
    item = make_request()
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, make_episode().pk)
    transition(users["ops"], item.pk, "delivered")
    for target in ["accepted", "rejected"]:
        with pytest.raises(DomainError):
            transition(users[actor], item.pk, target)


def test_delivery_threshold_and_task_quality_constraints(users, make_request, make_episode):
    item = make_request(episodes_requested=2)
    good = make_episode()
    with pytest.raises(DomainError):
        assign(users["ops"], item.pk, good.pk)
    transition(users["ops"], item.pk, "in_progress")
    for episode in [make_episode(quality="bad"), make_episode(task_name="fold towel")]:
        with pytest.raises(DomainError):
            assign(users["ops"], item.pk, episode.pk)
    assign(users["ops"], item.pk, good.pk)
    with pytest.raises(DomainError):
        transition(users["ops"], item.pk, "delivered")
    assign(users["ops"], item.pk, make_episode(quality="usable").pk)
    transition(users["ops"], item.pk, "delivered")


def test_request_can_receive_extra_clips_and_deliver(users, make_request, make_episode):
    item = make_request(episodes_requested=1)
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, make_episode().pk)
    assign(users["ops"], item.pk, make_episode().pk)
    assert item.assignments.count() == 2
    transition(users["ops"], item.pk, "delivered")


def test_rejection_requires_a_reason_and_audits_it(users, make_request, make_episode):
    item = make_request()
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, make_episode().pk)
    transition(users["ops"], item.pk, "delivered")
    with pytest.raises(DomainError, match="Explain what needs to change"):
        transition(users["client"], item.pk, "rejected")
    transition(users["client"], item.pk, "rejected", feedback="Please include another angle.")
    assert item.events.get(to_status="rejected").feedback == "Please include another angle."


def test_client_can_cancel_only_a_new_request_with_an_audited_reason(users, make_request):
    item = make_request()
    with pytest.raises(DomainError, match="Explain why you are cancelling"):
        transition(users["client"], item.pk, "cancelled")
    transition(users["client"], item.pk, "cancelled", feedback="The quantity needs to be revised.")
    item.refresh_from_db()
    assert item.status == "cancelled"
    event = item.events.get(to_status="cancelled")
    assert event.actor_id == users["client"].pk
    assert event.feedback == "The quantity needs to be revised."
    with pytest.raises(DomainError):
        transition(users["ops"], item.pk, "in_progress")


def test_cancellation_is_owner_only_and_database_transition_is_guarded(users, make_request):
    item = make_request()
    with pytest.raises(DomainError):
        transition(users["other"], item.pk, "cancelled", feedback="Not mine.")
    DatasetRequest.objects.filter(pk=item.pk).update(status="cancelled")
    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetRequest.objects.filter(pk=item.pk).update(status="in_progress")


def test_client_can_cancel_in_progress_request_before_clips_are_selected(users, make_request):
    item = make_request()
    transition(users["ops"], item.pk, "in_progress")

    transition(users["client"], item.pk, "cancelled", feedback="The task was entered with the wrong scope.")

    item.refresh_from_db()
    assert item.status == DatasetRequest.Status.CANCELLED


def test_in_progress_request_with_selected_clips_cannot_be_cancelled(users, make_request, make_episode):
    item = make_request()
    transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], item.pk, make_episode().pk)

    with pytest.raises(DomainError, match="selected clips cannot be cancelled"):
        transition(users["client"], item.pk, "cancelled", feedback="Changed plans.")

    with pytest.raises(IntegrityError), transaction.atomic():
        DatasetRequest.objects.filter(pk=item.pk).update(status="cancelled")


def test_episode_exclusivity_and_release(users, make_request, make_episode):
    first, second, episode = make_request(), make_request("other"), make_episode()
    for item in [first, second]:
        transition(users["ops"], item.pk, "in_progress")
    assign(users["ops"], first.pk, episode.pk)
    with pytest.raises(DomainError):
        assign(users["ops2"], second.pk, episode.pk)
    unassign(users["ops"], first.pk, episode.pk)
    assign(users["ops2"], second.pk, episode.pk)
    assert Assignment.objects.get(episode=episode).request_id == second.pk


def test_audit_failure_rolls_back_transition(users, make_request):
    item = make_request()
    with patch.object(StatusEvent.objects, "create", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError):
            transition(users["ops"], item.pk, "in_progress")
    item.refresh_from_db()
    assert item.status == "submitted"


def test_database_guards_alternate_write_paths(users, make_request, make_episode):
    item, good, bad = make_request(), make_episode(), make_episode(quality="bad")

    def rejected(action):
        with pytest.raises(IntegrityError), transaction.atomic():
            action()

    rejected(lambda: DatasetRequest.objects.filter(pk=item.pk).update(status="accepted"))
    rejected(lambda: Assignment.objects.create(request=item, episode=good, assigned_by=users["ops"]))
    transition(users["ops"], item.pk, "in_progress")
    rejected(lambda: DatasetRequest.objects.filter(pk=item.pk).update(status="delivered"))
    rejected(lambda: Assignment.objects.create(request=item, episode=bad, assigned_by=users["ops"]))
    mismatch = make_episode(task_name="other task")
    rejected(lambda: Assignment.objects.create(request=item, episode=mismatch, assigned_by=users["ops"]))
    assign(users["ops"], item.pk, good.pk)
    extra = make_episode()
    Assignment.objects.create(request=item, episode=extra, assigned_by=users["ops"])
    rejected(lambda: type(good).objects.filter(pk=good.pk).update(quality="bad"))
    rejected(lambda: DatasetRequest.objects.filter(pk=item.pk).update(episodes_requested=0))
    rejected(lambda: User.objects.filter(pk=users["client"].pk).update(role="operator"))
    transition(users["ops"], item.pk, "delivered")
    rejected(lambda: Assignment.objects.filter(request=item).delete())


def test_user_admin_rules_and_stale_actor(users, make_request):
    item = make_request()
    with pytest.raises(DomainError):
        update_user(users["ops"], users["other"].pk, {"is_active": False})
    with pytest.raises(DomainError):
        update_user(users["admin"], users["admin"].pk, {"role": "operator"})
    with pytest.raises(DomainError):
        update_user(users["admin"], users["client"].pk, {"role": "operator"})
    update_user(users["admin"], users["ops"].pk, {"is_active": False})
    with pytest.raises(DomainError):
        transition(users["ops"], item.pk, "in_progress")
