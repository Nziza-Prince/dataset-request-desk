"""Streaming, insert-only import. One transaction per batch, bounded diagnostics."""
import csv
import math
import re
from collections import Counter
from datetime import datetime, timezone as dt_timezone
from itertools import islice

from django.db import transaction
from django.utils import timezone
from .forms import canonical_task
from .models import Episode, Robot
from .services import DomainError, current_actor, require_role

HEADER = ["episode_id", "robot_id", "task_name", "recorded_at", "duration_seconds", "operator_name", "quality"]
FIELDS = ["robot_id", "task_name", "recorded_at", "duration_seconds", "operator_name", "quality"]


def parse_row(row, robots, now):
    if len(row) != len(HEADER):
        raise ValueError("wrong_column_count")
    episode_id, robot, task, recorded, duration, operator, quality = [v.strip() for v in row]
    episode_id, robot, quality = episode_id.upper(), robot.lower(), quality.lower()
    if not episode_id:
        raise ValueError("missing_episode_id")
    if not re.fullmatch(r"EP-[0-9]{1,60}", episode_id):
        raise ValueError("invalid_episode_id")
    if not robot:
        raise ValueError("missing_robot")
    if robot not in robots:
        raise ValueError("unknown_robot")
    task = canonical_task(task)
    if not task or len(task) > 200 or len(operator) > 200:
        raise ValueError("invalid_task_or_operator")
    if quality not in ("good", "usable", "bad"):
        raise ValueError("invalid_quality")
    try:
        seconds = float(duration)
    except ValueError:
        raise ValueError("invalid_duration")
    if not math.isfinite(seconds) or not 0 < seconds <= 3600:
        raise ValueError("duration_out_of_range")
    try:
        # Explicit day-first legacy format; ISO dates must contain a time.
        if "/" in recorded:
            recorded_at = datetime.strptime(recorded, "%d/%m/%Y %H:%M")
        else:
            if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", recorded):
                raise ValueError
            recorded_at = datetime.fromisoformat(recorded)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=dt_timezone.utc)
        recorded_at = recorded_at.astimezone(dt_timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("invalid_recorded_at")
    if recorded_at > now:
        raise ValueError("future_recorded_at")
    return Episode(id=episode_id, robot_id=robot, task_name=task, recorded_at=recorded_at,
                   duration_seconds=seconds, operator_name=operator, quality=quality)


def import_episodes(stream, actor, *, now=None, detail_limit=1000):
    require_role(actor, "operator", "admin")
    reader = csv.reader(stream, strict=True)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise DomainError("CSV is empty or has an invalid header")
    if [h.strip().lstrip("\ufeff") for h in header] != HEADER:
        raise DomainError("CSV header must be: " + ",".join(HEADER))
    now = now or timezone.now()
    robots = set(Robot.objects.values_list("pk", flat=True))
    counts, details = Counter(), []

    def record(line, outcome, reason, episode_id=""):
        counts[outcome] += 1
        reasons[reason] += 1
        if len(details) < detail_limit:
            details.append({"line": line, "outcome": outcome, "reason": reason, "episode_id": episode_id[:64]})

    reasons = Counter()

    def rows():
        try:
            for row in reader:
                yield reader.line_num, row
        except csv.Error:
            # Broken quoting has no reliable next record boundary: stop, report partial progress.
            record(reader.line_num, "skipped", "malformed_csv_remainder_not_read")

    source = rows()
    while batch := list(islice(source, 500)):
        candidates = []
        for line, row in batch:
            if not row or all(not value.strip() for value in row):
                record(line, "skipped", "blank_row")
                continue
            try:
                candidates.append((line, parse_row(row, robots, now)))
            except ValueError as exc:
                record(line, "skipped", str(exc), row[0] if row else "")
        with transaction.atomic():
            current_actor(actor, "operator", "admin")
            existing = Episode.objects.in_bulk([ep.pk for _, ep in candidates])
            inserts = []
            for line, episode in candidates:
                old = existing.get(episode.pk)
                if old:
                    identical = all(getattr(old, key) == getattr(episode, key) for key in FIELDS)
                    record(line, "skipped", "duplicate" if identical else "conflicting_duplicate", episode.pk)
                else:
                    existing[episode.pk] = episode
                    inserts.append(episode)
                    record(line, "imported", "new_episode", episode.pk)
            Episode.objects.bulk_create(inserts, batch_size=100)
    return {"imported": counts["imported"], "skipped": counts["skipped"], "reasons": dict(reasons),
            "details": sorted(details, key=lambda item: item["line"]),
            "details_truncated": sum(counts.values()) > len(details)}
