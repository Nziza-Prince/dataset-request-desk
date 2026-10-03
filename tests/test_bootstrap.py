import io
import pytest
from django.core.management import call_command
from desk.models import Episode, User

pytestmark = pytest.mark.django_db


def test_bootstrap_preserves_existing_passwords_roles_and_deactivation():
    call_command("seed_demo", with_episodes=True, stdout=io.StringIO())
    operator = User.objects.get(username="ops1@example.com")
    assert operator.check_password("ops123") and operator.password != "ops123"
    episode_count = Episode.objects.count()
    assert episode_count > 0
    operator.is_active = False
    operator.role = "client"
    operator.set_password("changed-password-42")
    operator.save()
    call_command("seed_demo", with_episodes=True, stdout=io.StringIO())
    operator.refresh_from_db()
    assert not operator.is_active and operator.role == "client"
    assert operator.check_password("changed-password-42")
    assert User.objects.count() == 5 and Episode.objects.count() == episode_count
