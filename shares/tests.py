import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Share, ShareFile, normalize_code

TMP_MEDIA = tempfile.mkdtemp()
User = get_user_model()
PNG = (b"\x89PNG\r\n\x1a\n" + b"0" * 200)


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class DropCodeTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user("alice", password="pw-alice-123")
        self.bob = User.objects.create_user("bob", password="pw-bob-123")

    def make_share(self, owner=None, **kw):
        share = Share.objects.create(owner=owner or self.alice, **kw)
        ShareFile.objects.create(
            share=share, file=SimpleUploadedFile("a.png", PNG), original_name="a.png",
            content_type="image/png", size=len(PNG))
        return share

    # --- codes ---
    def test_normalize_code(self):
        self.assertEqual(normalize_code("  Pomme 482 "), "pomme-482")
        self.assertEqual(normalize_code("pomme_482"), "pomme-482")

    def test_generated_code_format(self):
        s = Share.objects.create(owner=self.alice)
        self.assertRegex(s.code, r"^[a-z]+-\d{3}$")

    # --- côté acheteur ---
    def test_unlock_with_loose_input(self):
        s = self.make_share()
        r = self.client.post(reverse("home"), {"code": s.code.upper().replace("-", " ")})
        self.assertRedirects(r, reverse("view_share", args=[s.code]))
        s.refresh_from_db()
        self.assertEqual(s.view_count, 1)

    def test_wrong_and_expired_codes_rejected(self):
        s = self.make_share(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.post(reverse("home"), {"code": s.code}).status_code, 404)
        self.assertEqual(self.client.get(reverse("view_share", args=[s.code])).status_code, 404)
        self.assertEqual(self.client.post(reverse("home"), {"code": "nope-000"}).status_code, 404)

    def test_file_requires_valid_code(self):
        s = self.make_share()
        f = s.files.first()
        ok = self.client.get(reverse("shared_file", args=[s.code, f.pk]))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(b"".join(ok.streaming_content), PNG)
        self.assertEqual(ok["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get(reverse("shared_file", args=["faux-123", f.pk])).status_code, 404)

    def test_range_request(self):
        s = self.make_share()
        f = s.files.first()
        r = self.client.get(reverse("shared_file", args=[s.code, f.pk]), HTTP_RANGE="bytes=0-9")
        self.assertEqual(r.status_code, 206)
        self.assertEqual(b"".join(r.streaming_content), PNG[:10])
        self.assertEqual(r["Content-Range"], f"bytes 0-9/{len(PNG)}")
        bad = self.client.get(reverse("shared_file", args=[s.code, f.pk]), HTTP_RANGE="bytes=9999-")
        self.assertEqual(bad.status_code, 416)

    def test_bruteforce_lockout(self):
        for _ in range(8):
            self.client.post(reverse("home"), {"code": "faux-111"})
        s = self.make_share()
        r = self.client.post(reverse("home"), {"code": s.code})
        self.assertEqual(r.status_code, 429)

    # --- côté vendeur ---
    def test_dashboard_requires_login(self):
        r = self.client.get(reverse("dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])

    def test_register_creates_account_and_logs_in(self):
        r = self.client.post(reverse("register"), {
            "username": "carol", "password1": "S3cure-pass-xyz", "password2": "S3cure-pass-xyz"})
        self.assertRedirects(r, reverse("dashboard"))

    def test_create_share_and_history(self):
        self.client.force_login(self.alice)
        r = self.client.post(reverse("create_share"), {
            "title": "Canapé", "expiry_days": "3",
            "files": [SimpleUploadedFile("p.png", PNG), SimpleUploadedFile("v.mp4", b"0" * 50)]})
        share = Share.objects.get(owner=self.alice)
        self.assertRedirects(r, reverse("share_detail", args=[share.pk]))
        self.assertEqual(share.files.count(), 2)
        self.assertContains(self.client.get(reverse("dashboard")), share.code)

    def test_rejects_disallowed_files(self):
        self.client.force_login(self.alice)
        r = self.client.post(reverse("create_share"), {
            "files": [SimpleUploadedFile("x.html", b"<script>alert(1)</script>"),
                      SimpleUploadedFile("x.svg", b"<svg onload=alert(1)>")]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Share.objects.exists())

    def test_disk_name_is_random_and_type_from_extension(self):
        self.client.force_login(self.alice)
        self.client.post(reverse("create_share"), {
            "files": [SimpleUploadedFile("../../evil.PNG", PNG, content_type="text/html")]})
        sf = ShareFile.objects.get()
        self.assertEqual(sf.content_type, "image/png")
        self.assertNotIn("evil", sf.file.name)
        self.assertEqual(sf.original_name, "evil.PNG")

    def test_owner_isolation(self):
        s = self.make_share(owner=self.alice)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("share_detail", args=[s.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("delete_share", args=[s.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("owner_file", args=[s.pk, s.files.first().pk])).status_code, 404)
        self.assertTrue(Share.objects.filter(pk=s.pk).exists())

    def test_delete_share_removes_files_from_disk(self):
        s = self.make_share()
        path = s.files.first().file.path
        self.client.force_login(self.alice)
        self.client.post(reverse("delete_share", args=[s.pk]))
        self.assertFalse(Share.objects.filter(pk=s.pk).exists())
        import os
        self.assertFalse(os.path.exists(path))

    def test_extend_reactivates_expired_share(self):
        s = self.make_share(expires_at=timezone.now() - timedelta(days=1))
        self.client.force_login(self.alice)
        self.client.post(reverse("extend_share", args=[s.pk]), {"expiry_days": "7"})
        s.refresh_from_db()
        self.assertFalse(s.is_expired)
