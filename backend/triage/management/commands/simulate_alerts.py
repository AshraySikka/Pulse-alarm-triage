import time

from django.core.management.base import BaseCommand

from triage.simulator import tick


class Command(BaseCommand):
    help = "Stream simulated alerts into the database (the UI's Live feed switch does the same thing)."

    def add_arguments(self, parser):
        parser.add_argument("--every", type=float, default=5.0, help="Seconds between ticks")
        parser.add_argument("--ticks", type=int, default=0, help="Stop after this many ticks (0 = run until Ctrl+C)")

    def handle(self, *args, **opts):
        n = 0
        try:
            while True:
                out = tick()
                n += 1
                self.stdout.write(f"tick {n}: {out['alerts']} alerts, {out['grouped']} grouped, "
                                  f"{out['new_incidents']} new incidents")
                if opts["ticks"] and n >= opts["ticks"]:
                    break
                time.sleep(opts["every"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
