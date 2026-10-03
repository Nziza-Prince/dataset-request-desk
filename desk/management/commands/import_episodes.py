import json
from django.core.management.base import BaseCommand, CommandError
from desk.importing import import_episodes
from desk.models import User
from desk.services import DomainError, require_role


class Command(BaseCommand):
    help = "Import CSV as an active operator/admin. Database/host access is trusted."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--actor", required=True, help="Existing operator/admin email")

    def handle(self, *args, **options):
        try:
            actor = User.objects.get(username=options["actor"].strip().lower())
            require_role(actor, "operator", "admin")
            with open(options["path"], encoding="utf-8-sig", newline="") as stream:
                report = import_episodes(stream, actor)
        except (User.DoesNotExist, OSError, UnicodeError, DomainError) as exc:
            raise CommandError(str(exc))
        self.stdout.write(json.dumps(report, indent=2))
