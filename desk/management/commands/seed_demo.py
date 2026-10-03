import json
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from desk.models import Robot, User
from desk.importing import import_episodes


class Command(BaseCommand):
    help = "Create the supplied demo accounts and known robots; never reset existing users."

    def add_arguments(self, parser):
        parser.add_argument("--with-episodes", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        for robot in ["arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01"]:
            Robot.objects.get_or_create(pk=robot)
        users = json.loads((settings.BASE_DIR / "seed/users.json").read_text())
        count = 0
        import_actor = None
        for data in users:
            email = data["email"].lower()
            if User.objects.filter(username=email).exists():
                continue
            user = User.objects.create_user(username=email, email=email, password=data["password"],
                first_name=data["name"], role=data["role"], organisation=data.get("organisation", ""))
            if email == "ops1@example.com":
                import_actor = user
            count += 1
        self.stdout.write(f"Created {count} demo users (existing accounts left unchanged).")
        # Only initial bootstrap imports demo episodes. Restart must not depend on
        # a demo operator remaining active or retaining its original role.
        if options["with_episodes"] and import_actor:
            with (settings.BASE_DIR / "seed/episodes.csv").open(encoding="utf-8-sig", newline="") as stream:
                report = import_episodes(stream, import_actor)
            self.stdout.write(json.dumps(report))
