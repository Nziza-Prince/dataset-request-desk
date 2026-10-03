from django import forms
from datetime import date
from django.utils import timezone
from .models import DatasetRequest, Episode, User


def canonical_task(value):
    return " ".join(value.split()).casefold()


class RequestForm(forms.Form):
    task_name = forms.CharField(max_length=200)
    episodes_requested = forms.IntegerField(
        min_value=1,
        max_value=1000000,
        widget=forms.NumberInput(attrs={"step": 1, "inputmode": "numeric"}),
    )
    deadline = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, max_length=5000, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deadline"].widget.attrs["min"] = timezone.localdate().isoformat()
        self.available_task_names = tuple(
            Episode.objects.filter(quality__in=["good", "usable"])
            .order_by("task_name")
            .values_list("task_name", flat=True)
            .distinct()
        )
        choices = [("", "Choose an action…")]
        choices.extend((task, task.capitalize()) for task in self.available_task_names)
        self.fields["task_name"].widget = forms.Select(choices=choices)

    def clean_task_name(self):
        value = canonical_task(self.cleaned_data["task_name"])
        if self.available_task_names and value not in self.available_task_names:
            raise forms.ValidationError("Choose one of the available actions.")
        return value

    def clean_deadline(self):
        value = self.cleaned_data["deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("Deadline must be today or later (Kigali time).")
        return value


class UserForm(forms.Form):
    email = forms.EmailField(max_length=150)
    name = forms.CharField(max_length=150)
    password = forms.CharField(max_length=128, widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=User.Role.choices)
    organisation = forms.CharField(max_length=200, required=False)


class AnalyticsForm(forms.Form):
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end"):
            if data["end"] == date.max:
                raise forms.ValidationError("end must be before 9999-12-31")
            if data["start"] > data["end"]:
                raise forms.ValidationError("start must be on or before end")
            if (data["end"] - data["start"]).days > 366:
                raise forms.ValidationError("Date range is limited to 367 inclusive days")
        return data


class QueueFilterForm(forms.Form):
    status = forms.ChoiceField(required=False, choices=[("", "All statuses"),
        *[(status, DatasetRequest(status=status).status_label) for status in DatasetRequest.Status.values]])
    deadline = forms.ChoiceField(required=False, choices=[("", "All deadlines"), ("overdue", "Overdue, awaiting delivery")])
    sort = forms.ChoiceField(required=False, choices=[("", "Newest first"), ("deadline", "Earliest deadline first")])


class EpisodeUploadForm(forms.Form):
    file = forms.FileField(label="Episode CSV", widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}))

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Choose a CSV no larger than 2 MiB. Use the import command for larger files.")
        return upload
