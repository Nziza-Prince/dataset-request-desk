import io
from datetime import datetime, timezone
from pathlib import Path
import pytest
from desk.importing import HEADER, import_episodes
from desk.models import Episode, Robot
from desk.services import DomainError

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def import_text(text, actor, **kwargs):
    return import_episodes(io.StringIO(",".join(HEADER) + "\n" + text), actor, now=NOW, **kwargs)


def test_real_seed_import_twice(users):
    for robot in ["arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01"]:
        Robot.objects.create(pk=robot)
    text = Path("seed/episodes.csv").read_text()
    first = import_episodes(io.StringIO(text), users["ops"], now=NOW)
    before = list(Episode.objects.order_by("id").values())
    second = import_episodes(io.StringIO(text), users["ops"], now=NOW)
    assert (first["imported"], first["skipped"]) == (173, 18)
    assert first["reasons"]["duplicate"] == 2
    assert first["reasons"]["conflicting_duplicate"] == 2
    assert second["imported"] == 0 and second["skipped"] == 191
    assert list(Episode.objects.order_by("id").values()) == before
    assert Episode.objects.get(pk="EP-00011").quality == "bad"
    assert Episode.objects.get(pk="EP-00003").robot_id == "humanoid-01"
    assert Episode.objects.get(pk="EP-00018").duration_seconds == 45.5
    assert Episode.objects.get(pk="EP-90002").task_name == "pick cup, then place"
    assert Episode.objects.get(pk="EP-90005").operator_name == ""
    assert Episode.objects.get(pk="EP-00014").recorded_at.isoformat() == "2026-08-14T09:15:00+00:00"
    assert Episode.objects.get(pk="EP-00006").task_name == "pick cup"
    assert Episode.objects.get(pk="EP-00010").quality == "usable"


@pytest.mark.parametrize("duration", ["0", "-1", "NaN", "inf", "-inf", "3601", "N/A", ""])
def test_invalid_durations(users, duration):
    Robot.objects.create(pk="arm-01")
    report = import_text(f"EP-1,arm-01,pick cup,2026-08-01T10:00:00,{duration},A,good\n", users["ops"])
    assert report["imported"] == 0 and report["skipped"] == 1


def test_normalized_duplicate_offsets_and_first_valid_row(users):
    Robot.objects.create(pk="arm-01")
    report = import_text(
        "EP-1,arm-01,pick cup,invalid,20,A,good\n"
        "ep-1, ARM-01 , Pick   Cup ,2026-08-01T12:00:00+02:00,20,A,GOOD\n"
        "EP-1,arm-01,pick cup,2026-08-01T10:00:00Z,20.0,A,good\n", users["ops"])
    assert report["imported"] == 1
    assert report["reasons"] == {"invalid_recorded_at": 1, "new_episode": 1, "duplicate": 1}


def test_import_diagnostics_bounded_and_batches_idempotent(users):
    Robot.objects.create(pk="arm-01")
    rows = "".join(f"EP-{i},arm-01,pick cup,2026-08-01T10:00:00,20,A,good\n" for i in range(510))
    first = import_text(rows, users["ops"], detail_limit=5)
    second = import_text(rows, users["ops"], detail_limit=5)
    assert first["imported"] == 510 and len(first["details"]) == 5 and first["details_truncated"]
    assert second["imported"] == 0 and second["reasons"]["duplicate"] == 510


def test_bad_header_and_malformed_remainder(users):
    with pytest.raises(DomainError):
        import_episodes(io.StringIO("wrong,header\n"), users["ops"])
    Robot.objects.create(pk="arm-01")
    report = import_text('EP-1,arm-01,pick cup,2026-08-01T10:00:00,20,A,good\n"unterminated', users["ops"])
    assert report["imported"] == 1
    assert report["reasons"]["malformed_csv_remainder_not_read"] == 1


def test_client_cannot_import(users):
    Robot.objects.create(pk="arm-01")
    with pytest.raises(DomainError):
        import_text("EP-1,arm-01,pick cup,2026-08-01T10:00:00,20,A,good\n", users["client"])
    assert Episode.objects.count() == 0
