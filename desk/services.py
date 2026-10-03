from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from .forms import RequestForm, UserForm
from .models import Assignment, DatasetRequest, Episode, StatusEvent, User


class DomainError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def require_role(user, *roles):
    if not user.is_authenticated:
        raise DomainError("Authentication required", 401)
    if not user.is_active or user.role not in roles:
        raise DomainError("Not permitted", 403)


def current_actor(user, *roles):
    # Called inside write transactions: role/deactivation cannot race a mutation.
    actor = User.objects.get(pk=user.pk)
    require_role(actor, *roles)
    return actor


def visible_requests(user):
    require_role(user, "client", "operator", "admin")
    qs = DatasetRequest.objects.all()
    return qs.filter(client=user) if user.role == "client" else qs


def get_request(user, pk):
    if pk > 2**63 - 1:
        raise DomainError("Request not found", 404)
    try:
        return visible_requests(user).get(pk=pk)
    except DatasetRequest.DoesNotExist:
        raise DomainError("Request not found", 404)


def validated(form):
    if not form.is_valid():
        raise DomainError(form.errors.get_json_data())
    return form.cleaned_data


@transaction.atomic
def create_request(user, data):
    actor = current_actor(user, "client")
    values = validated(RequestForm(data))
    request = DatasetRequest.objects.create(client=actor, **values)
    StatusEvent.objects.create(request=request, from_status="", to_status="submitted", actor=actor)
    return request


@transaction.atomic
def transition(user, pk, target, *, feedback=""):
    actor = current_actor(user, "client", "operator", "admin")
    request = get_request(actor, pk)
    allowed = {
        ("submitted", "in_progress"): {"operator", "admin"},
        ("in_progress", "delivered"): {"operator", "admin"},
        ("delivered", "accepted"): {"client"},
        ("delivered", "rejected"): {"client"},
        ("rejected", "in_progress"): {"operator", "admin"},
        ("submitted", "cancelled"): {"client"},
        ("in_progress", "cancelled"): {"client"},
    }
    roles = allowed.get((request.status, target))
    if roles is None:
        raise DomainError("Invalid or stale status transition; refresh the request", 409)
    require_role(actor, *roles)
    if target == "cancelled" and request.status == "in_progress" and request.assignments.exists():
        raise DomainError("A request with selected clips cannot be cancelled", 409)
    if not isinstance(feedback, str) or len(feedback.strip()) > 2000:
        raise DomainError("Feedback must be text of at most 2000 characters")
    feedback = feedback.strip()
    if target in ("rejected", "cancelled") and not feedback:
        message = "Explain why you are cancelling this request" if target == "cancelled" else "Explain what needs to change before requesting changes"
        raise DomainError(message)
    if target not in ("rejected", "cancelled") and feedback:
        raise DomainError("Feedback is only supported when requesting changes or cancelling")
    if target == "delivered" and request.assignments.count() < request.episodes_requested:
        raise DomainError("Assign at least the requested number of clips before sending for review", 409)
    previous = request.status
    request.status = target
    request.save(update_fields=["status"])
    StatusEvent.objects.create(request=request, from_status=previous, to_status=target, actor=actor, feedback=feedback)
    return request


@transaction.atomic
def assign(user, pk, episode_id):
    actor = current_actor(user, "operator", "admin")
    request = get_request(actor, pk)
    if request.status != "in_progress":
        raise DomainError("Assignments can only change while in progress", 409)
    try:
        episode = Episode.objects.get(pk=episode_id.strip().upper())
    except Episode.DoesNotExist:
        raise DomainError("Episode not found", 404)
    if episode.quality not in ("good", "usable"):
        raise DomainError("Bad episodes cannot be assigned", 409)
    if episode.task_name != request.task_name:
        raise DomainError("Episode task must match the request task", 409)
    existing = Assignment.objects.filter(episode=episode).first()
    if existing:
        if existing.request_id == request.pk:
            return existing  # Retrying the same assignment is harmless.
        raise DomainError("Episode is already assigned to another request", 409)
    return Assignment.objects.create(episode=episode, request=request, assigned_by=actor)


@transaction.atomic
def unassign(user, pk, episode_id):
    actor = current_actor(user, "operator", "admin")
    request = get_request(actor, pk)
    if request.status != "in_progress":
        raise DomainError("Assignments can only change while in progress", 409)
    Assignment.objects.filter(request=request, episode_id=episode_id.strip().upper()).delete()


@transaction.atomic
def create_user(user, data):
    current_actor(user, "admin")
    values = validated(UserForm(data))
    email = values["email"].lower()
    new_user = User(username=email, email=email, first_name=values["name"],
                    role=values["role"], organisation=values["organisation"])
    try:
        validate_password(values["password"], new_user)
    except ValidationError as exc:
        raise DomainError(exc.messages)
    if User.objects.filter(username=email).exists():
        raise DomainError("Email already exists", 409)
    new_user.set_password(values["password"])
    new_user.save()
    return new_user


@transaction.atomic
def update_user(user, pk, data):
    actor = current_actor(user, "admin")
    if pk > 2**63 - 1:
        raise DomainError("User not found", 404)
    try:
        target = User.objects.get(pk=pk)
    except User.DoesNotExist:
        raise DomainError("User not found", 404)
    if set(data) - {"role", "is_active"} or not data:
        raise DomainError("Only role and is_active may be changed")
    role = data.get("role", target.role)
    active = data.get("is_active", target.is_active)
    if role not in User.Role.values or type(active) is not bool:
        raise DomainError("Invalid role or is_active (must be boolean)")
    if target.pk == actor.pk and (not active or role != "admin"):
        raise DomainError("Administrators cannot demote or deactivate themselves", 409)
    if target.role == "client" and role != "client" and target.requests.exists():
        raise DomainError("A client with requests must remain a client; deactivate instead", 409)
    target.role, target.is_active = role, active
    target.save(update_fields=["role", "is_active"])
    return target
