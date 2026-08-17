import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from django.core import signing
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.test import APIClient

from app.models import NaturalPerson
from api.auth.binding import BINDING_SIGNING_SALT, issue_binding_credential
from api.config import CONFIG
from generic.models import (
    PendingWechatBinding,
    User,
    UserWechatProfile,
)


urlpatterns = [
    path("api/", include("api.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
]


def concurrent_bind(barrier, payload):
    close_old_connections()
    try:
        client = APIClient()
        barrier.wait()
        return client.post(
            "/api/v2/auth/wx/bind/", payload, format="json"
        ).status_code
    finally:
        close_old_connections()


@override_settings(ROOT_URLCONF="api.auth.tests")
class WechatBindingSchemaTestCase(TestCase):
    def test_schema_describes_one_time_binding_credential(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"one-time", response.content)
        self.assertIn(b"signed_openid", response.content)


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


class WechatBindingApiTestCase(TestCase):
    def setUp(self):
        self.password = "valid-v18-password"
        self.user = User.objects.create_user(
            "v18-user", "V18 User", User.Type.PERSON,
            password=self.password, is_newuser=False,
        )
        NaturalPerson.objects.create(self.user, name="V18 User")

    def issue(self, openid="openid-v18"):
        with patch(
            "api.auth.views._fetch_openid_from_wechat",
            return_value=(openid, None),
        ):
            response = self.client.post(
                "/api/v2/auth/wx/login/", {"code": "fresh-code"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unbound")
        return response.json()["signed_openid"]

    def bind(self, credential, username=None, password=None):
        return self.client.post("/api/v2/auth/wx/bind/", {
            "signed_openid": credential,
            "username": username or self.user.username,
            "password": password or self.password,
        })

    def test_forged_credential_is_rejected(self):
        self.assertEqual(self.bind("forged").status_code, 400)

    def test_expired_database_credential_is_deleted_and_rejected(self):
        credential = self.issue()
        fixed_now = datetime(2100, 1, 1, 12, 0)
        PendingWechatBinding.objects.update(
            expires_at=fixed_now - timedelta(seconds=1)
        )
        with patch("api.auth.binding.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = fixed_now
            response = self.bind(credential)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PendingWechatBinding.objects.exists())

    def test_success_consumes_credential_and_replay_fails(self):
        credential = self.issue()
        first = self.bind(credential)
        second = self.bind(credential)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertFalse(PendingWechatBinding.objects.exists())
        self.assertEqual(UserWechatProfile.objects.get().openid, "openid-v18")

    def test_five_failed_passwords_exhaust_credential(self):
        credential = self.issue()
        for attempt in range(5):
            response = self.bind(credential, password="wrong-password")
            self.assertEqual(response.status_code, 401, attempt)
        self.assertFalse(PendingWechatBinding.objects.exists())
        self.assertEqual(self.bind(credential).status_code, 400)
        self.assertFalse(UserWechatProfile.objects.exists())

    def test_existing_profile_is_not_rebound(self):
        UserWechatProfile.objects.create(
            user=self.user,
            openid="existing-openid",
        )
        credential = self.issue("new-openid")
        self.assertEqual(self.bind(credential).status_code, 400)
        self.assertEqual(self.user.wx_profile.openid, "existing-openid")


class WechatBindingConcurrencyTestCase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.password = "valid-v18-password"
        self.user = User.objects.create_user(
            "v18-race-a", "V18 Race A", User.Type.PERSON,
            password=self.password, is_newuser=False,
        )
        NaturalPerson.objects.create(self.user, name="V18 Race A")
        self.other_user = User.objects.create_user(
            "v18-race-b", "V18 Race B", User.Type.PERSON,
            password=self.password, is_newuser=False,
        )
        NaturalPerson.objects.create(self.other_user, name="V18 Race B")

    def issue(self, openid):
        with patch(
            "api.auth.views._fetch_openid_from_wechat",
            return_value=(openid, None),
        ):
            response = APIClient().post(
                "/api/v2/auth/wx/login/",
                {"code": "fresh-code"},
                format="json",
            )
        self.assertEqual(response.status_code, 200)
        return response.json()["signed_openid"]

    def run_race(self, credential, usernames):
        barrier = Barrier(2)
        payloads = [
            {
                "signed_openid": credential,
                "username": username,
                "password": self.password,
            }
            for username in usernames
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(concurrent_bind, barrier, payload)
                for payload in payloads
            ]
        return [future.result() for future in futures]

    def assert_single_winner(self, statuses, openid):
        self.assertEqual(statuses.count(200), 1)
        self.assertNotIn(500, statuses)
        self.assertEqual(
            UserWechatProfile.objects.filter(openid=openid).count(), 1
        )
        self.assertFalse(PendingWechatBinding.objects.exists())

    def test_same_user_same_credential_has_one_winner(self):
        openid = "openid-v18-race-same"
        credential = self.issue(openid)
        statuses = self.run_race(
            credential, [self.user.username, self.user.username]
        )
        self.assert_single_winner(statuses, openid)

    def test_different_users_same_credential_have_one_winner(self):
        openid = "openid-v18-race-different"
        credential = self.issue(openid)
        statuses = self.run_race(
            credential, [self.user.username, self.other_user.username]
        )
        self.assert_single_winner(statuses, openid)
