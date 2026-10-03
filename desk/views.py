import io
from datetime import timedelta, timezone as dt_timezone
from functools import wraps
from django.contrib import messages
from django.contrib.auth import logout
from django.core.paginator import Paginator
from django.db import connection, OperationalError
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from . import services
from .api import available_episodes, endpoint
from .authentication import sign_in
from .analytics import analytics
from .forms import AnalyticsForm, EpisodeUploadForm, QueueFilterForm, RequestForm
from .importing import import_episodes
from .models import DatasetRequest, Episode


def csrf_failure(request, reason=""):
    if request.path.startswith("/api/"):
        return JsonResponse({"error": "CSRF verification failed; obtain a fresh token"}, status=403)
    return render(request, "desk/error.html", {"error": "Your form expired. Reload the page and try again."}, status=403)


def with_feasibility(queryset):
    """Attach matching unreserved inventory without one query per request."""
    available_matches = Episode.objects.filter(
        assignment__isnull=True,
        quality__in=["good", "usable"],
        task_name=OuterRef("task_name"),
    ).values("task_name").annotate(total=Count("id")).values("total")[:1]
    return queryset.annotate(
        assigned_count=Count("assignments"),
        unassigned_matching_count=Coalesce(
            Subquery(available_matches, output_field=IntegerField()), Value(0), output_field=IntegerField()
        ),
    )


def set_feasibility(item, assigned_count=None):
    assigned_count = item.assigned_count if assigned_count is None else assigned_count
    available = getattr(item, "unassigned_matching_count", None)
    if available is None:
        available = Episode.objects.filter(
            assignment__isnull=True, quality__in=["good", "usable"], task_name=item.task_name
        ).count()
    item.available_matching_count = available
    item.feasible_capacity = assigned_count + available
    item.shortfall = max(item.episodes_requested - item.feasible_capacity, 0)
    item.needs_feasibility_review = (
        item.shortfall > 0 and item.status in ("submitted", "in_progress", "rejected")
    )


def authenticated(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        try:
            return view(request, *args, **kwargs)
        except services.DomainError as exc:
            return render(request, "desk/error.html", {"error": exc.message}, status=exc.status)
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            return render(request, "desk/error.html", {"error": "Database busy; retry shortly."}, status=503)
    return wrapped


@require_http_methods(["GET", "POST"])
def login_view(request):
    error, status = None, 200
    if request.method == "POST":
        try:
            sign_in(request, request.POST)
            return redirect("home")
        except services.DomainError as exc:
            error, status = exc.message, exc.status
    return render(request, "desk/login.html", {"error": error}, status=status)


@require_http_methods(["POST"])
@authenticated
def logout_view(request):
    logout(request)
    return redirect("login")


@require_http_methods(["GET", "POST"])
@authenticated
def home(request):
    initial = {}
    if request.method == "GET" and request.GET.get("new"):
        initial = {key: request.GET.get(key, "") for key in ("task_name", "episodes_requested", "deadline")}
    form = RequestForm(request.POST or None, initial=initial)
    if request.method == "POST":
        services.require_role(request.user, "client")
        if form.is_valid():
            item = services.create_request(request.user, request.POST)
            return redirect("detail", pk=item.pk)
    qs = with_feasibility(services.visible_requests(request.user).select_related("client")).order_by("-id")
    filters = QueueFilterForm(request.GET)
    if filters.is_valid():
        values = filters.cleaned_data
        if values["status"]:
            qs = qs.filter(status=values["status"])
        if values["deadline"] == "overdue":
            qs = qs.filter(deadline__lt=timezone.localdate(), status__in=["submitted", "in_progress", "rejected"])
        if values["sort"] == "deadline":
            qs = qs.order_by("deadline", "id")
    else:
        qs = qs.none()
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    for item in page:
        set_feasibility(item)
        remaining = max(item.episodes_requested - item.assigned_count, 0)
        item.extra_count = max(item.assigned_count - item.episodes_requested, 0)
        item.overdue = item.deadline < timezone.localdate() and item.status in ("submitted", "in_progress", "rejected")
        item.next_action = {
            "submitted": "Waiting for an operator to start",
            "in_progress": f"Select {remaining} more clip{'s' if remaining != 1 else ''}" if remaining else "Ready to send for review",
            "delivered": "Waiting for the client to review",
            "accepted": "Delivery complete",
            "rejected": "Read the feedback and start rework",
            "cancelled": "Request cancelled",
        }[item.status]
        if request.user.role == "client":
            item.next_action = {
                "submitted": "The operations team will pick up your request",
                "in_progress": "The operations team is preparing your delivery",
                "delivered": "Review the clips and accept or request changes",
                "accepted": "Delivery complete",
                "rejected": "Waiting for the operations team to make changes",
                "cancelled": "Request cancelled",
            }[item.status]
    return render(request, "desk/home.html", {
        "form": form,
        "page": page,
        "filters": filters,
        "request_count": page.paginator.count,
    })


@require_http_methods(["GET", "POST"])
@authenticated
def detail(request, pk):
    item = services.get_request(request.user, pk)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "transition":
                target = request.POST.get("status", "")
                services.transition(request.user, pk, target, feedback=request.POST.get("feedback", ""))
                success = {
                    "in_progress": "Work started. You can now select clips.",
                    "delivered": "The selected clips were sent to the client for review.",
                    "accepted": "Delivery accepted. This request is complete.",
                    "rejected": "Changes requested. The operator can reopen the request for rework.",
                    "cancelled": "Request cancelled. You can create a revised replacement below.",
                }.get(target, "Request updated.")
            elif action == "assign":
                services.assign(request.user, pk, request.POST.get("episode_id", ""))
                success = "Clip added to this request."
            elif action == "unassign":
                services.unassign(request.user, pk, request.POST.get("episode_id", ""))
                success = "Clip removed. You can choose a replacement."
            else:
                raise services.DomainError("Unknown action")
            messages.success(request, success)
        except services.DomainError as exc:
            messages.error(request, str(exc))
        return redirect("detail", pk=pk)
    staff = request.user.role in ("operator", "admin")
    candidates = None
    assigned_count = item.assignments.count()
    set_feasibility(item, assigned_count)
    if staff and item.status == "in_progress":
        filters = {"task_name": request.GET.get("task_name", item.task_name), "quality": request.GET.get("quality", "")}
        candidates = Paginator(available_episodes(filters).filter(task_name=item.task_name).order_by("id"), 25).get_page(request.GET.get("page"))
    return render(request, "desk/detail.html", {
        "item": item, "staff": staff, "candidates": candidates,
        "assigned_count": assigned_count,
        "remaining_count": max(item.episodes_requested - assigned_count, 0),
        "extra_count": max(assigned_count - item.episodes_requested, 0),
        "available_matching_count": item.available_matching_count,
        "feasible_capacity": item.feasible_capacity,
        "shortfall": item.shortfall,
        "needs_feasibility_review": item.needs_feasibility_review,
        "latest_feedback": item.events.filter(to_status="rejected").order_by("-id").first() if item.status in ("rejected", "in_progress") else None,
        "assignments": Paginator(item.assignments.select_related("episode").order_by("episode_id"), 25).get_page(request.GET.get("assigned_page")),
        "events": Paginator(item.events.select_related("actor").order_by("-id"), 25).get_page(request.GET.get("activity_page")),
    })


@require_http_methods(["GET", "POST"])
@authenticated
def import_view(request):
    services.require_role(request.user, "operator", "admin")
    form = EpisodeUploadForm(request.POST if request.method == "POST" else None, request.FILES)
    report = None
    if request.method == "POST" and form.is_valid():
        try:
            # Decode before importing so invalid UTF-8 never commits part of a file.
            content = form.cleaned_data["file"].read().decode("utf-8-sig")
            report = import_episodes(io.StringIO(content, newline=""), request.user)
        except UnicodeError:
            form.add_error("file", "CSV must use UTF-8 encoding. No rows were imported.")
        except services.DomainError as exc:
            form.add_error("file", str(exc) + " Earlier completed batches, if any, remain imported; re-uploading is safe.")
    return render(request, "desk/import.html", {"form": form, "report": report})


@require_http_methods(["GET"])
@authenticated
def analytics_view(request):
    services.require_role(request.user, "operator", "admin")
    today = timezone.now().astimezone(dt_timezone.utc).date()
    params = request.GET if request.GET else {"start": today - timedelta(days=29), "end": today}
    form = AnalyticsForm(params)
    report = analytics(request.user, params) if form.is_valid() else None
    status_counts = []
    if report:
        status_counts = [{"status": status, "label": DatasetRequest(status=status).status_label, "count": count}
                         for status, count in report["requests_by_status"].items()]
    return render(request, "desk/analytics.html", {
        "form": form, "report": report, "status_counts": status_counts,
        "recordings": Paginator(report["episodes_per_day_robot"] if report else [], 25).get_page(request.GET.get("page")),
        "request_count": sum(row["count"] for row in status_counts),
        "episode_count": sum(row["count"] for row in report["episodes_per_day_robot"]) if report else 0,
    })


@endpoint(["GET"])
def health(request):
    # Deliberately authenticated, including operational endpoints per the brief.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM desk_user LIMIT 1")
            cursor.fetchone()
    except OperationalError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
