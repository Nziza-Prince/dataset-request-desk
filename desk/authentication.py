import hashlib
from datetime import timedelta
from django.contrib.auth import authenticate, login
from django.db import transaction
from django.utils import timezone
from .models import LoginAttempt
from .services import DomainError


def sign_in(request, data):
    email, password = data.get("email", ""), data.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str) or len(email) > 150 or len(password) > 128:
        raise DomainError("Invalid credentials", 401)
    email = email.strip().lower()
    key = hashlib.sha256(email.encode()).hexdigest()
    now = timezone.now()
    # Reserve an attempt before expensive hashing; serializes across workers.
    with transaction.atomic():
        LoginAttempt.objects.filter(started_at__lt=now - timedelta(minutes=15)).delete()
        attempt, _ = LoginAttempt.objects.get_or_create(key=key, defaults={"started_at": now})
        if attempt.failures >= 10:
            raise DomainError("Too many login attempts; try again in 15 minutes", 429)
        attempt.failures += 1
        attempt.save(update_fields=["failures"])
    user = authenticate(request, username=email, password=password)
    if user is None:
        raise DomainError("Invalid credentials", 401)
    LoginAttempt.objects.filter(pk=key).delete()
    login(request, user)
    return user
