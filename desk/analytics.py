from datetime import datetime, time, timedelta, timezone
from django.db import connection
from django.db.models import Count
from django.db.models.functions import TruncDate
from .forms import AnalyticsForm
from .models import DatasetRequest, Episode
from .services import require_role, validated


def analytics(user, params):
    require_role(user, "operator", "admin")
    dates = validated(AnalyticsForm(params))
    start = datetime.combine(dates["start"], time.min, tzinfo=timezone.utc)
    end = datetime.combine(dates["end"] + timedelta(days=1), time.min, tzinfo=timezone.utc)
    episodes = Episode.objects.filter(recorded_at__gte=start, recorded_at__lt=end)
    requests = DatasetRequest.objects.filter(created_at__gte=start, created_at__lt=end)
    counts = dict(requests.values("status").annotate(total=Count("id")).values_list("status", "total"))
    # Median is computed entirely in SQL, including an even-sized cohort. First
    # delivery avoids inflating cycle time when a client asks for rework.
    with connection.cursor() as cursor:
        cursor.execute("""
            WITH durations AS (
                SELECT (julianday(MIN(e.created_at)) - julianday(r.created_at)) * 86400.0 AS seconds
                FROM desk_datasetrequest r
                JOIN desk_statusevent e ON e.request_id = r.id AND e.to_status = 'delivered'
                WHERE r.created_at >= %s AND r.created_at < %s
                GROUP BY r.id
            ), ranked AS (
                SELECT seconds, ROW_NUMBER() OVER (ORDER BY seconds) AS position,
                       COUNT(*) OVER () AS total FROM durations
            )
            SELECT AVG(seconds) FROM ranked
            WHERE position IN ((total + 1) / 2, (total + 2) / 2)
        """, [start.replace(tzinfo=None), end.replace(tzinfo=None)])
        median = cursor.fetchone()[0]
    return {
        "start": dates["start"], "end": dates["end"], "timezone": "UTC",
        "episodes_per_day_robot": list(episodes.annotate(day=TruncDate("recorded_at", tzinfo=timezone.utc))
            .values("day", "robot_id").annotate(count=Count("id")).order_by("day", "robot_id")),
        "requests_by_status": {status: counts.get(status, 0) for status in DatasetRequest.Status.values},
        "median_seconds_to_first_delivery": median,
        "top_good_tasks": list(episodes.filter(quality="good").values("task_name")
            .annotate(count=Count("id")).order_by("-count", "task_name")[:5]),
    }
