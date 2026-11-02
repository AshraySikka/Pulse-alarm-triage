from django.core.management.base import BaseCommand

from triage.seed import seed_demo


class Command(BaseCommand):
    help = "Wipe demo data and generate a fresh synthetic shift ending now."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=None, help="Random seed for a repeatable shift")
        parser.add_argument("--reset-config", action="store_true", help="Also reset the model settings to defaults")

    def handle(self, *args, **opts):
        out = seed_demo(seed=opts["seed"], keep_config=not opts["reset_config"])
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {out['patients']} patients, {out['alerts']} alerts in the last 8 hours, "
            f"{out['incidents']} incidents (including 30 days of closed history)."
        ))
