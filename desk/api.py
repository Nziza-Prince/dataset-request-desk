import io
import json
from functools import wraps
from django.contrib.auth import logout
from django.core.exceptions import RequestDataTooBig
from django.db import IntegrityError, OperationalError
from django.db.models import Count
from django.http import JsonResponse
from django.middleware.csrf import get_token
from . import services
from .analytics import analytics
from .authentication import sign_in
from .forms import canonical_task
from .importing import import_episodes
from .models import Episode, User


def endpoint(methods, roles=("client", "operator", "admin"), public=False):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    response = JsonResponse({"error": "Method not allowed"}, status=405)
                    response["Allow"] = ", ".join(methods)
                    return response
                if not public:
                    services.require_role(request.user, *roles)
                return view(request, *args, **kwargs)
            except services.DomainError as exc:
                return JsonResponse({"error": exc.message}, status=exc.status)
            except RequestDataTooBig:
                return JsonResponse({"error": "Request body exceeds 2 MiB"}, status=413)
            except IntegrityError:
                return JsonResponse({"error": "Operation conflicts with a database constraint"}, status=409)
            except OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                response = JsonResponse({"error": "Database busy; retry shortly"}, status=503)
                response["Retry-After"] = "2"
                return response
        return wrapped
    return decorate


def body(request, allowed):
    if request.content_type != "application/json":
        raise services.DomainError("Content-Type must be application/json", 415)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError):
        raise services.DomainError("Malformed JSON")
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise services.DomainError("Expected an object containing only: " + ", ".join(allowed))
    # Keep JSON types explicit; Django Forms otherwise coerce text fields.
    if any(isinstance(v, (list, dict)) or v is None for v in data.values()):
        raise services.DomainError("Fields must be scalar values")
    for key, value in data.items():
        if key not in {"episodes_requested", "is_active"} and not isinstance(value, str):
            raise services.DomainError(f"{key} must be a string")
    return data


def user_json(user):
    return {"id": user.pk, "email": user.email, "name": user.first_name, "role": user.role, "is_active": user.is_active}


def request_json(item):
    return {"id": item.pk, "client_id": item.client_id, "task_name": item.task_name,
            "episodes_requested": item.episodes_requested, "deadline": item.deadline,
            "notes": item.notes, "status": item.status, "created_at": item.created_at,
            "assigned_count": item.assigned_count if hasattr(item, "assigned_count") else item.assignments.count()}


def page(request, queryset, serializer=lambda item: item, key="pk", numeric=True):
    after = request.GET.get("after")
    if after:
        if numeric:
            try:
                after = int(after)
                if after < 0 or after > 2**63 - 1:
                    raise ValueError
            except ValueError:
                raise services.DomainError("after must be a nonnegative integer")
        queryset = queryset.filter(**{key + "__gt": after})
    items = list(queryset.order_by(key)[:51])
    last = items[49] if len(items) > 50 else None
    cursor = (last[key] if isinstance(last, dict) else getattr(last, key)) if last is not None else None
    return {"results": [serializer(item) for item in items[:50]], "next_after": cursor}


@endpoint(["GET"], public=True)
def csrf(request):
    # Token bootstrap is part of login; no domain data is public.
    return JsonResponse({"csrf_token": get_token(request)})


@endpoint(["POST"], public=True)
def login_api(request):
    user = sign_in(request, body(request, ["email", "password"]))
    return JsonResponse({"user": user_json(user), "csrf_token": get_token(request)})


@endpoint(["POST"])
def logout_api(request):
    logout(request)
    return JsonResponse({"ok": True})


@endpoint(["GET"])
def me(request):
    return JsonResponse(user_json(request.user))


@endpoint(["GET", "POST"])
def requests_api(request):
    if request.method == "POST":
        data = body(request, ["task_name", "episodes_requested", "deadline", "notes"])
        if type(data.get("episodes_requested")) is not int:
            raise services.DomainError("episodes_requested must be an integer")
        return JsonResponse(request_json(services.create_request(request.user, data)), status=201)
    qs = services.visible_requests(request.user).annotate(assigned_count=Count("assignments"))
    return JsonResponse(page(request, qs, request_json))


@endpoint(["GET"])
def request_detail(request, pk):
    item = services.get_request(request.user, pk)
    return JsonResponse(request_json(item))


@endpoint(["POST"])
def transition_api(request, pk):
    data = body(request, ["status", "feedback"])
    if not isinstance(data.get("status"), str):
        raise services.DomainError("status must be a string")
    return JsonResponse(request_json(services.transition(request.user, pk, data["status"], feedback=data.get("feedback", ""))))


@endpoint(["GET"])
def events_api(request, pk):
    item = services.get_request(request.user, pk)
    return JsonResponse(page(request, item.events.values("id", "from_status", "to_status", "actor_id", "created_at", "feedback"), key="id"))


@endpoint(["GET", "POST"])
def assignments_api(request, pk):
    item = services.get_request(request.user, pk)
    if request.method == "POST":
        data = body(request, ["episode_id"])
        if not isinstance(data.get("episode_id"), str):
            raise services.DomainError("episode_id must be a string")
        assignment = services.assign(request.user, pk, data["episode_id"])
        return JsonResponse({"episode_id": assignment.episode_id, "request_id": assignment.request_id})
    return JsonResponse(page(request, item.assignments.values("episode_id", "assigned_by_id", "assigned_at"), key="episode_id", numeric=False))


@endpoint(["DELETE"], roles=("operator", "admin"))
def assignment_delete(request, pk, episode_id):
    services.unassign(request.user, pk, episode_id)
    return JsonResponse({"ok": True})


def available_episodes(params):
    qs = Episode.objects.filter(assignment__isnull=True, quality__in=["good", "usable"])
    task, quality = params.get("task_name", ""), params.get("quality", "")
    if task:
        qs = qs.filter(task_name=canonical_task(task))
    if quality:
        if quality not in ("good", "usable"):
            raise services.DomainError("quality must be good or usable")
        qs = qs.filter(quality=quality)
    return qs


@endpoint(["GET"], roles=("operator", "admin"))
def episodes_api(request):
    return JsonResponse(page(request, available_episodes(request.GET).values(
        "id", "robot_id", "task_name", "quality", "recorded_at", "duration_seconds", "operator_name"), key="id", numeric=False))


@endpoint(["POST"], roles=("operator", "admin"))
def import_api(request):
    if request.content_type != "text/csv":
        raise services.DomainError("Send CSV as a UTF-8 text/csv request body", 415)
    try:
        stream = io.StringIO(request.body.decode("utf-8-sig"), newline="")
    except UnicodeError:
        raise services.DomainError("CSV must use UTF-8 encoding")
    return JsonResponse(import_episodes(stream, request.user))


@endpoint(["GET"], roles=("operator", "admin"))
def analytics_api(request):
    return JsonResponse(analytics(request.user, request.GET))


@endpoint(["GET", "POST"], roles=("admin",))
def users_api(request):
    if request.method == "POST":
        user = services.create_user(request.user, body(request, ["email", "name", "password", "role", "organisation"]))
        return JsonResponse(user_json(user), status=201)
    return JsonResponse(page(request, User.objects.all(), user_json))


@endpoint(["PATCH"], roles=("admin",))
def user_update(request, pk):
    return JsonResponse(user_json(services.update_user(request.user, pk, body(request, ["role", "is_active"]))))
