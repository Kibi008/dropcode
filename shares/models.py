import os
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from .words import WORDS

ALLOWED_EXTENSIONS = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "avif": "image/avif", "heic": "image/heic", "heif": "image/heif",
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
}

EXPIRY_CHOICES = [(1, "1 jour"), (3, "3 jours"), (7, "7 jours"), (30, "30 jours")]


def normalize_code(raw: str) -> str:
    """'  Pomme 482 ' -> 'pomme-482'. Tolère espaces, underscores, casse."""
    code = re.sub(r"[\s_]+", "-", (raw or "").strip().lower())
    return re.sub(r"-{2,}", "-", code)


def generate_code() -> str:
    for _ in range(50):
        code = f"{secrets.choice(WORDS)}-{secrets.randbelow(900) + 100}"
        if not Share.objects.filter(code=code).exists():
            return code
    raise RuntimeError("Impossible de générer un code unique.")


def default_expiry():
    return timezone.now() + timedelta(days=settings.DROPCODE_DEFAULT_EXPIRY_DAYS)


def upload_path(instance, filename):
    # Nom disque aléatoire : l'extension est conservée, jamais le nom d'origine.
    ext = os.path.splitext(filename)[1].lower()
    return f"shares/{instance.share.pk}/{uuid.uuid4().hex}{ext}"


class Share(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shares")
    title = models.CharField("Titre", max_length=120, blank=True)
    code = models.CharField(max_length=40, unique=True, default=generate_code)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expiry)
    view_count = models.PositiveIntegerField(default=0)
    last_viewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or self.code

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def display_title(self):
        return self.title or "Sans titre"


class ShareFile(models.Model):
    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=upload_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]

    @property
    def is_video(self):
        return self.content_type.startswith("video/")

    @property
    def is_browser_image(self):
        return self.content_type in {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}


@receiver(post_delete, sender=ShareFile)
def delete_file_from_disk(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
