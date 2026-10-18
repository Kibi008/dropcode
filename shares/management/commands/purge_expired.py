from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from shares.models import Share


class Command(BaseCommand):
    help = "Supprime les dossiers expirés (et leurs fichiers) depuis plus de N jours."

    def add_arguments(self, parser):
        parser.add_argument("--grace-days", type=int, default=7,
                            help="Délai de grâce après expiration avant suppression (défaut : 7).")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=opts["grace_days"])
        qs = Share.objects.filter(expires_at__lt=cutoff)
        n = qs.count()
        for share in qs:
            share.delete()
        self.stdout.write(self.style.SUCCESS(f"{n} dossier(s) supprimé(s)."))
