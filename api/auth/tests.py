import hashlib
from datetime import datetime, timedelta
from unittest.mock import patch

from django.core import signing
from django.test import TestCase

from api.auth.binding import BINDING_SIGNING_SALT, issue_binding_credential
from api.config import CONFIG
from generic.models import PendingWechatBinding


class WechatBindingIssuanceTestCase(TestCase):
    def test_issue_stores_only_nonce_digest_with_expiry(self):
        now = datetime(2026, 8, 17, 12, 0)
        with patch("api.auth.binding.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            credential = issue_binding_credential("openid-v18")

        nonce = signing.TimestampSigner(salt=BINDING_SIGNING_SALT).unsign(
            credential,
            max_age=CONFIG.signed_openid_ttl_minutes * 60,
        )
        pending = PendingWechatBinding.objects.get()
        self.assertEqual(pending.openid, "openid-v18")
        self.assertEqual(
            pending.nonce_digest,
            hashlib.sha256(nonce.encode()).hexdigest(),
        )
        self.assertNotEqual(pending.nonce_digest, nonce)
        self.assertNotIn(nonce, pending.openid)
        self.assertEqual(pending.failed_attempts, 0)
        self.assertEqual(
            pending.expires_at,
            now + timedelta(minutes=CONFIG.signed_openid_ttl_minutes),
        )

    def test_issue_cleans_expired_rows_and_keeps_unexpired_rows(self):
        now = datetime(2026, 8, 17, 12, 0)
        expired = PendingWechatBinding.objects.create(
            nonce_digest="e" * 64,
            openid="expired",
            expires_at=now - timedelta(seconds=1),
        )
        unexpired = PendingWechatBinding.objects.create(
            nonce_digest="u" * 64,
            openid="unexpired",
            expires_at=now + timedelta(seconds=1),
        )
        with patch("api.auth.binding.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            issue_binding_credential("openid-v18")
        self.assertFalse(PendingWechatBinding.objects.filter(pk=expired.pk).exists())
        self.assertTrue(PendingWechatBinding.objects.filter(pk=unexpired.pk).exists())

    def test_issue_rolls_back_cleanup_when_creation_fails(self):
        now = datetime(2026, 8, 17, 12, 0)
        expired = PendingWechatBinding.objects.create(
            nonce_digest="e" * 64,
            openid="expired",
            expires_at=now - timedelta(seconds=1),
        )
        with patch("api.auth.binding.datetime") as mocked_datetime, patch(
            "api.auth.binding.PendingWechatBinding.objects.create",
            side_effect=RuntimeError("create failed"),
        ):
            mocked_datetime.now.return_value = now
            with self.assertRaisesRegex(RuntimeError, "create failed"):
                issue_binding_credential("openid-v18")
        self.assertTrue(PendingWechatBinding.objects.filter(pk=expired.pk).exists())
