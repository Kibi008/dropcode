import re
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import EXPIRY_CHOICES, Share, ShareFile, normalize_code
from .uploads import save_uploads


# ---------- utilitaires ----------

def client_ip(request):
    # Derrière un proxy de confiance, adaptez ici (X-Forwarded-For).
    return request.META.get("REMOTE_ADDR", "unknown")


def _fail_key(request):
    return f"dropcode-fail-{client_ip(request)}"


def is_locked(request):
    return cache.get(_fail_key(request), 0) >= settings.DROPCODE_MAX_FAILED_ATTEMPTS


def register_failure(request):
    key = _fail_key(request)
    cache.add(key, 0, settings.DROPCODE_LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, settings.DROPCODE_LOCKOUT_SECONDS)


def find_active_share(raw_code):
    code = normalize_code(raw_code)
    if not code:
        return None
    share = Share.objects.filter(code=code).first()
    if share is None or share.is_expired:
        return None
    return share


def unlocked_share_or_404(request, code):
    """Le code figure dans l'URL : c'est lui le secret. Même 404 pour 'inconnu' et 'expiré'."""
    if is_locked(request):
        raise Http404
    share = find_active_share(code)
    if share is None:
        register_failure(request)
        raise Http404
    return share


# ---------- côté acheteur (public) ----------

def home(request):
    if request.method == "POST":
        if is_locked(request):
            messages.error(request, "Trop d'essais. Réessayez dans quelques minutes.")
            return render(request, "shares/home.html", status=429)
        raw = request.POST.get("code", "")
        share = find_active_share(raw)
        if share is None:
            register_failure(request)
            messages.error(request, "Code inconnu ou expiré. Vérifiez-le auprès du vendeur.")
            return render(request, "shares/home.html", {"typed": raw}, status=404)
        cache.delete(_fail_key(request))
        Share.objects.filter(pk=share.pk).update(
            view_count=share.view_count + 1, last_viewed_at=timezone.now()
        )
        return redirect("view_share", code=share.code)
    return render(request, "shares/home.html")


def view_share(request, code):
    share = unlocked_share_or_404(request, code)
    response = render(request, "shares/view_share.html", {"share": share, "files": share.files.all()})
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def _serve_file(request, sf, as_attachment=False):
    """Sert un fichier avec support des requêtes Range (nécessaire à la lecture vidéo sur iOS)."""
    try:
        fh = sf.file.open("rb")
    except FileNotFoundError:
        raise Http404
    size = sf.size or sf.file.size
    start, end, status = 0, size - 1, 200

    m = _RANGE_RE.match(request.headers.get("Range", "").strip())
    if m and size:
        s, e = m.groups()
        if s == "" and e != "":          # suffixe : les N derniers octets
            start = max(size - int(e), 0)
        elif s != "":
            start = int(s)
            end = min(int(e), size - 1) if e != "" else size - 1
        if start > end or start >= size:
            fh.close()
            resp = HttpResponse(status=416)
            resp["Content-Range"] = f"bytes */{size}"
            return resp
        status = 206

    length = end - start + 1
    fh.seek(start)

    def chunks(remaining=length, chunk=64 * 1024):
        try:
            while remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            fh.close()

    resp = FileResponse(chunks(), status=status, content_type=sf.content_type)
    resp["Content-Length"] = str(length)
    resp["Accept-Ranges"] = "bytes"
    if status == 206:
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    disposition = "attachment" if as_attachment else "inline"
    safe_name = re.sub(r'[^\w.\- ]', "_", sf.original_name)
    resp["Content-Disposition"] = f'{disposition}; filename="{safe_name}"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, max-age=3600"
    resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


def serve_shared_file(request, code, file_id):
    share = unlocked_share_or_404(request, code)
    sf = get_object_or_404(ShareFile, pk=file_id, share=share)
    return _serve_file(request, sf, as_attachment=request.GET.get("dl") == "1")


# ---------- côté vendeur (compte) ----------

def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


@login_required
def dashboard(request):
    shares = request.user.shares.prefetch_related("files")
    return render(request, "shares/dashboard.html", {"shares": shares})


@login_required
def create_share(request):
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            messages.error(request, "Ajoutez au moins une photo ou une vidéo.")
        else:
            try:
                days = int(request.POST.get("expiry_days", settings.DROPCODE_DEFAULT_EXPIRY_DAYS))
            except ValueError:
                days = settings.DROPCODE_DEFAULT_EXPIRY_DAYS
            if days not in dict(EXPIRY_CHOICES):
                days = settings.DROPCODE_DEFAULT_EXPIRY_DAYS
            share = Share.objects.create(
                owner=request.user,
                title=request.POST.get("title", "").strip()[:120],
                expires_at=timezone.now() + timedelta(days=days),
            )
            saved, errors = save_uploads(share, files)
            for err in errors:
                messages.warning(request, err)
            if saved == 0:
                share.delete()
                messages.error(request, "Aucun fichier valide : le dossier n'a pas été créé.")
            else:
                messages.success(request, f"Dossier créé. Code à communiquer : {share.code}")
                return redirect("share_detail", pk=share.pk)
    return render(request, "shares/create.html", {
        "expiry_choices": EXPIRY_CHOICES,
        "default_days": settings.DROPCODE_DEFAULT_EXPIRY_DAYS,
        "max_mb": settings.DROPCODE_MAX_FILE_MB,
        "max_files": settings.DROPCODE_MAX_FILES_PER_SHARE,
    })


@login_required
def share_detail(request, pk):
    share = get_object_or_404(Share, pk=pk, owner=request.user)
    if request.method == "POST":
        saved, errors = save_uploads(share, request.FILES.getlist("files"))
        for err in errors:
            messages.warning(request, err)
        if saved:
            messages.success(request, f"{saved} fichier(s) ajouté(s).")
        return redirect("share_detail", pk=share.pk)
    return render(request, "shares/detail.html", {
        "share": share,
        "files": share.files.all(),
        "expiry_choices": EXPIRY_CHOICES,
    })


@login_required
@require_POST
def extend_share(request, pk):
    share = get_object_or_404(Share, pk=pk, owner=request.user)
    try:
        days = int(request.POST.get("expiry_days", 0))
    except ValueError:
        days = 0
    if days in dict(EXPIRY_CHOICES):
        share.expires_at = timezone.now() + timedelta(days=days)
        share.save(update_fields=["expires_at"])
        messages.success(request, "Durée de validité mise à jour.")
    return redirect("share_detail", pk=share.pk)


@login_required
@require_POST
def delete_share_file(request, pk, file_id):
    share = get_object_or_404(Share, pk=pk, owner=request.user)
    get_object_or_404(ShareFile, pk=file_id, share=share).delete()
    messages.success(request, "Fichier supprimé.")
    return redirect("share_detail", pk=share.pk)


@login_required
@require_POST
def delete_share(request, pk):
    share = get_object_or_404(Share, pk=pk, owner=request.user)
    share.delete()  # les fichiers disparaissent du disque via le signal post_delete
    messages.success(request, "Dossier supprimé. Son code ne fonctionne plus.")
    return redirect("dashboard")


@login_required
def owner_preview_file(request, pk, file_id):
    """Aperçu des fichiers pour le propriétaire, sans passer par le code."""
    share = get_object_or_404(Share, pk=pk, owner=request.user)
    sf = get_object_or_404(ShareFile, pk=file_id, share=share)
    return _serve_file(request, sf)
