# DropCode

Partagez photos et vidéos avec un **code court et temporaire** (ex. `pomme-482`) au lieu d'un lien.
Pensé pour les messageries qui bloquent les URL (Leboncoin, etc.).

- **Acheteur** : va sur le site, saisit le code, voit la galerie. Aucun compte requis.
- **Vendeur** : crée un compte, dépose ses fichiers, reçoit un code, retrouve l'historique
  (nombre d'ouvertures, expiration), peut prolonger, ajouter/supprimer des fichiers ou le dossier.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Ouvrir http://127.0.0.1:8000 — tests : `python manage.py test`.

## Sécurité (ce qui est en place)

- Les fichiers ne sont **jamais** servis comme média public : chaque requête repasse par une vue qui vérifie le code.
- Noms de fichiers aléatoires sur disque ; type MIME déduit de l'extension validée (liste blanche photos/vidéos, pas de SVG/HTML).
- En-têtes `nosniff` + CSP `sandbox` sur les fichiers, pages en `no-store` / `noindex`.
- Anti force brute : 8 codes erronés par IP → blocage 15 min (cache mémoire ; passez à Redis/Memcached si plusieurs processus).
- Un code = 100 mots × 900 nombres ≈ 90 000 combinaisons : suffisant pour des photos d'annonce avec expiration courte,
  **pas** pour des documents sensibles. Augmentez `WORDS` ou ajoutez un 2ᵉ mot dans `models.generate_code` si besoin.
- Code inconnu et code expiré renvoient la même réponse (pas de fuite d'information).

## Mise en production

Variables d'environnement :

| Variable | Rôle |
|---|---|
| `DJANGO_DEBUG=0` | désactive le mode debug |
| `DJANGO_SECRET_KEY` | **obligatoire** hors debug |
| `DJANGO_ALLOWED_HOSTS` | ex. `drop.example.fr` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | ex. `https://drop.example.fr` |
| `DROPCODE_MEDIA_ROOT` | dossier de stockage des fichiers (hors du dépôt) |

```bash
python manage.py collectstatic --noinput
gunicorn config.wsgi
```

Derrière nginx/Caddy en HTTPS : limitez `client_max_body_size` (≥ 200 Mo pour les vidéos) et, si le proxy transmet
`X-Forwarded-For`, adaptez `client_ip()` dans `shares/views.py` pour que l'anti force brute voie la vraie IP.
Servez `/static/` via le proxy (ou WhiteNoise).

Nettoyage (cron quotidien) : `python manage.py purge_expired --grace-days 7`.

## Réglages (`config/settings.py`)

`DROPCODE_MAX_FILE_MB` (200), `DROPCODE_MAX_FILES_PER_SHARE` (30), `DROPCODE_DEFAULT_EXPIRY_DAYS` (7),
`DROPCODE_MAX_FAILED_ATTEMPTS` (8), `DROPCODE_LOCKOUT_SECONDS` (900).
