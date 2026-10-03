from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        CLIENT = "client"
        OPERATOR = "operator"
        ADMIN = "admin"

    # username is the normalized email, retaining Django's tested auth backend.
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role, default=Role.CLIENT)
    organisation = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(role__in=["client", "operator", "admin"]), name="valid_role")]


class Robot(models.Model):
    id = models.CharField(primary_key=True, max_length=40)


class Episode(models.Model):
    id = models.CharField(primary_key=True, max_length=64)
    robot = models.ForeignKey(Robot, on_delete=models.PROTECT)
    task_name = models.CharField(max_length=200, db_index=True)
    recorded_at = models.DateTimeField(db_index=True)
    duration_seconds = models.FloatField()
    operator_name = models.CharField(max_length=200, blank=True)
    quality = models.CharField(max_length=6)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(quality__in=["good", "usable", "bad"]), name="valid_quality"),
            models.CheckConstraint(condition=Q(duration_seconds__gt=0, duration_seconds__lte=3600), name="sensible_duration"),
        ]
        indexes = [models.Index(fields=["recorded_at", "task_name"], condition=Q(quality="good"), name="good_recorded_task")]


class DatasetRequest(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted"
        IN_PROGRESS = "in_progress"
        DELIVERED = "delivered"
        ACCEPTED = "accepted"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    client = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requests")
    task_name = models.CharField(max_length=200)
    episodes_requested = models.PositiveIntegerField()
    deadline = models.DateField()
    notes = models.TextField(blank=True, max_length=5000)
    status = models.CharField(max_length=12, choices=Status, default=Status.SUBMITTED)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(episodes_requested__gte=1, episodes_requested__lte=1000000), name="valid_requested_count"),
            models.CheckConstraint(condition=Q(status__in=["submitted", "in_progress", "delivered", "accepted", "rejected", "cancelled"]), name="valid_status"),
        ]

    @property
    def status_label(self):
        return {
            self.Status.SUBMITTED: "New request",
            self.Status.IN_PROGRESS: "In progress",
            self.Status.DELIVERED: "Ready for review",
            self.Status.ACCEPTED: "Accepted",
            self.Status.REJECTED: "Changes requested",
            self.Status.CANCELLED: "Cancelled",
        }[self.status]

    @property
    def status_message(self):
        return {
            self.Status.SUBMITTED: "Waiting for an operator to start selecting clips.",
            self.Status.IN_PROGRESS: "An operator is selecting and preparing the requested clips.",
            self.Status.DELIVERED: "The clips are ready. The client can accept them or request changes.",
            self.Status.ACCEPTED: "The client approved this delivery. This request is complete.",
            self.Status.REJECTED: "The client requested changes. An operator needs to start rework.",
            self.Status.CANCELLED: "The client cancelled this request before any clips were selected.",
        }[self.status]


class Assignment(models.Model):
    # The primary key enforces the global, concurrent one-request-per-episode rule.
    episode = models.OneToOneField(Episode, primary_key=True, on_delete=models.PROTECT)
    request = models.ForeignKey(DatasetRequest, related_name="assignments", on_delete=models.PROTECT)
    assigned_by = models.ForeignKey(User, on_delete=models.PROTECT)
    assigned_at = models.DateTimeField(auto_now_add=True)


class StatusEvent(models.Model):
    request = models.ForeignKey(DatasetRequest, related_name="events", on_delete=models.PROTECT)
    feedback = models.TextField(blank=True, default="", max_length=2000)
    from_status = models.CharField(max_length=12, blank=True)
    to_status = models.CharField(max_length=12)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["request", "to_status", "created_at"], name="event_delivery_lookup")]

    @property
    def activity_title(self):
        if self.to_status == DatasetRequest.Status.SUBMITTED:
            return "Request created"
        if self.to_status == DatasetRequest.Status.IN_PROGRESS:
            return "Rework started" if self.from_status == DatasetRequest.Status.REJECTED else "Work started"
        return {
            DatasetRequest.Status.DELIVERED: "Clips sent for review",
            DatasetRequest.Status.ACCEPTED: "Delivery accepted",
            DatasetRequest.Status.REJECTED: "Changes requested",
            DatasetRequest.Status.CANCELLED: "Request cancelled",
        }.get(self.to_status, "Request updated")

    @property
    def activity_description(self):
        name = self.actor.first_name or self.actor.email
        if self.to_status == DatasetRequest.Status.SUBMITTED:
            return f"{name} submitted the request."
        if self.to_status == DatasetRequest.Status.IN_PROGRESS:
            action = "started reworking the delivery" if self.from_status == DatasetRequest.Status.REJECTED else "started selecting matching clips"
            return f"{name} {action}."
        if self.to_status == DatasetRequest.Status.DELIVERED:
            return f"{name} sent the selected clips to the client."
        if self.to_status == DatasetRequest.Status.ACCEPTED:
            return f"{name} approved the delivered clips."
        if self.to_status == DatasetRequest.Status.REJECTED:
            return f"{name} asked the operator to make changes."
        if self.to_status == DatasetRequest.Status.CANCELLED:
            return f"{name} cancelled the request."
        return f"{name} updated the request."


class LoginAttempt(models.Model):
    # A bounded-time account throttle shared by workers, without trusting proxy headers.
    key = models.CharField(primary_key=True, max_length=64)
    failures = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
