"""Validation et enregistrement des fichiers envoyés."""
import os

from django.conf import settings

from .models import ALLOWED_EXTENSIONS, ShareFile


def save_uploads(share, uploaded_files):
    """Valide puis enregistre les fichiers. Retourne (nb_enregistrés, [erreurs])."""
    errors = []
    max_bytes = settings.DROPCODE_MAX_FILE_MB * 1024 * 1024
    room = settings.DROPCODE_MAX_FILES_PER_SHARE - share.files.count()
    saved = 0

    for f in uploaded_files:
        if room <= 0:
            errors.append(
                f"Limite de {settings.DROPCODE_MAX_FILES_PER_SHARE} fichiers atteinte : « {f.name} » ignoré."
            )
            continue
        ext = os.path.splitext(f.name)[1].lower().lstrip(".")
        if ext not in ALLOWED_EXTENSIONS:
            errors.append(f"« {f.name} » : format non accepté (photos et vidéos uniquement).")
            continue
        if f.size > max_bytes:
            errors.append(f"« {f.name} » dépasse {settings.DROPCODE_MAX_FILE_MB} Mo.")
            continue
        ShareFile.objects.create(
            share=share,
            file=f,
            original_name=os.path.basename(f.name)[:255],
            # Le type est déduit de l'extension validée, jamais de l'en-tête envoyé par le client.
            content_type=ALLOWED_EXTENSIONS[ext],
            size=f.size,
        )
        saved += 1
        room -= 1
    return saved, errors
