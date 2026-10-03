from datetime import timedelta
import pytest
from django.utils import timezone
from desk.models import Episode, Robot, User
from desk.services import create_request


@pytest.fixture(autouse=True)
def fast_hashing(settings, tmp_path):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.STATIC_ROOT = tmp_path


@pytest.fixture
def users(db):
    return {key: User.objects.create_user(username=f"{key}@example.com", email=f"{key}@example.com",
                password="test-password-42", first_name=key, role=role)
            for key, role in [("client", "client"), ("other", "client"), ("ops", "operator"), ("ops2", "operator"), ("admin", "admin")]}


@pytest.fixture
def make_request(users):
    def make(owner="client", **kwargs):
        return create_request(users[owner], {"task_name": "pick cup", "episodes_requested": 1,
            "deadline": (timezone.localdate() + timedelta(days=30)).isoformat(), "notes": "", **kwargs})
    return make


@pytest.fixture
def make_episode(db):
    Robot.objects.get_or_create(pk="arm-01")
    counter = 0

    def make(**kwargs):
        nonlocal counter
        counter += 1
        return Episode.objects.create(**{
            "id": f"EP-{counter:05}", "robot_id": "arm-01", "task_name": "pick cup",
            "recorded_at": timezone.now() - timedelta(days=1), "duration_seconds": 30,
            "operator_name": "Test Operator", "quality": "good", **kwargs,
        })
    return make
