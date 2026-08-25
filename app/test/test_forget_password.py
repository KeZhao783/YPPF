from datetime import datetime, timedelta
import json
import uuid
from unittest.mock import Mock, patch

import requests
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import signing
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils.crypto import salted_hmac

from app import models, utils
from extern import password_reset
from extern.wechat import send_password_reset_token
from generic.models import User


class PasswordResetDomainTests(TestCase):
    def make_request(self, ip_address="192.0.2.10"):
        request = RequestFactory().post(
            "/forgetpw/", REMOTE_ADDR=ip_address)
        SessionMiddleware(lambda request: None).process_request(request)
        request.session.save()
        return request

    def setUp(self):
        self.now = datetime(2026, 8, 16, 12, 0, 0)
        self.user = User.objects.create_user(
            username="password-reset-user",
            name="Password Reset User",
            password="old-password",
        )
        self.request = self.make_request()

    def test_signed_token_resets_only_its_bound_user(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        challenge = models.PasswordResetChallenge.objects.get(user=self.user)

        self.assertNotIn(self.user.username, token)
        self.assertNotEqual(challenge.token_digest, token)
        self.assertTrue(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Secure-pass-123"))

    def test_token_accepts_database_equivalent_username_casing(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        self.assertTrue(utils.reset_password_from_token(
            self.request,
            self.user.username.upper(),
            token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_password_change_invalidates_an_outstanding_token(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        self.user.set_password("owner-secured-password")
        self.user.save(update_fields=["password"])

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Attacker-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("owner-secured-password"))

    def test_token_rejects_a_different_submitted_username(self):
        other_user = User.objects.create_user(
            username="other-reset-user",
            name="Other Reset User",
            password="other-password",
        )
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            other_user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        other_user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))
        self.assertTrue(other_user.check_password("other-password"))

    def test_token_rejects_a_different_browser_session(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        self.assertFalse(utils.reset_password_from_token(
            self.make_request(),
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))

    def test_token_rejects_a_different_ip_address(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        self.request.META["REMOTE_ADDR"] = "192.0.2.99"

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))

    def test_token_ignores_untrusted_forwarded_for(self):
        self.request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.1"
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        self.request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.99"

        self.assertTrue(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_token_rejects_a_different_signed_purpose(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        challenge = models.PasswordResetChallenge.objects.get(user=self.user)
        signed_value = signing.dumps(
            {
                "challenge": str(challenge.id),
                "user": self.user.pk,
                "purpose": "login",
            },
            salt="app.password-reset.token",
            compress=True,
        )
        wrong_purpose_token = f"{challenge.id}.{signed_value}"
        challenge.token_digest = salted_hmac(
            "app.password-reset.token-digest", wrong_purpose_token
        ).hexdigest()
        challenge.save(update_fields=["token_digest"])

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            wrong_purpose_token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_token_rejects_a_different_signed_user(self):
        other_user = User.objects.create_user(
            username="signed-other-user",
            name="Signed Other User",
            password="other-password",
        )
        utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        challenge = models.PasswordResetChallenge.objects.get(user=self.user)
        signed_value = signing.dumps(
            {
                "challenge": str(challenge.id),
                "user": other_user.pk,
                "purpose": "password-reset",
            },
            salt="app.password-reset.token",
            compress=True,
        )
        wrong_user_token = f"{challenge.id}.{signed_value}"
        challenge.token_digest = salted_hmac(
            "app.password-reset.token-digest", wrong_user_token
        ).hexdigest()
        challenge.save(update_fields=["token_digest"])

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            wrong_user_token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_expired_token_does_not_change_password(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now + timedelta(minutes=11),
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))

    def test_consumed_token_cannot_be_reused(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        self.assertTrue(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Another-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Secure-pass-123"))

    def test_fifth_bad_signature_invalidates_challenge(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        challenge_id, signed_value = token.split(".", 1)
        replacement = "x" if signed_value[-1] != "x" else "y"
        bad_token = f"{challenge_id}.{signed_value[:-1]}{replacement}"

        for _ in range(5):
            self.assertFalse(utils.reset_password_from_token(
                self.request,
                self.user.username,
                bad_token,
                "Secure-pass-123",
                now=self.now,
            ))

        challenge = models.PasswordResetChallenge.objects.get(pk=challenge_id)
        self.assertEqual(challenge.failed_attempts, 5)
        self.assertEqual(challenge.invalidated_at, self.now)
        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_fifth_token_failure_temporarily_locks_reset_flow(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        challenge_id, signed_value = token.split(".", 1)
        replacement = "x" if signed_value[-1] != "x" else "y"
        bad_token = f"{challenge_id}.{signed_value[:-1]}{replacement}"
        for _ in range(5):
            self.assertFalse(utils.reset_password_from_token(
                self.request,
                self.user.username,
                bad_token,
                "Secure-pass-123",
                now=self.now,
            ))

        locked_token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            locked_token,
            "Secure-pass-123",
            now=self.now,
        ))

        unlocked_at = self.now + timedelta(minutes=16)
        unlocked_token = utils.create_password_reset_token(
            self.request, self.user, now=unlocked_at)
        self.assertTrue(utils.reset_password_from_token(
            self.request,
            self.user.username,
            unlocked_token,
            "Secure-pass-123",
            now=unlocked_at,
        ))

    def test_fifth_failure_locks_challenge_target_account(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)

        for _ in range(5):
            self.assertFalse(utils.reset_password_from_token(
                self.request,
                "unrelated-submitted-user",
                token,
                "Secure-pass-123",
                now=self.now,
            ))

        fresh_request = self.make_request(ip_address="198.51.100.99")
        fresh_token = utils.create_password_reset_token(
            fresh_request, self.user, now=self.now)
        self.assertFalse(utils.reset_password_from_token(
            fresh_request,
            self.user.username,
            fresh_token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))

    def test_fourth_account_request_is_rate_limited(self):
        results = [
            utils.check_password_reset_request_rate(
                self.request, self.user.username, now=self.now)
            for _ in range(4)
        ]

        self.assertEqual(results, [True, True, True, False])

    def test_account_request_limit_uses_canonical_username(self):
        results = [
            utils.check_password_reset_request_rate(
                self.request,
                (
                    self.user.username.upper()
                    if index % 2
                    else self.user.username
                ),
                now=self.now,
            )
            for index in range(4)
        ]

        self.assertEqual(results, [True, True, True, False])
        self.assertEqual(
            models.PasswordResetThrottle.objects.filter(
                scope=models.PasswordResetThrottle.Scope.REQUEST_ACCOUNT,
            ).count(),
            1,
        )

    def test_sixth_device_request_is_rate_limited(self):
        results = [
            utils.check_password_reset_request_rate(
                self.request, f"device-user-{index}", now=self.now)
            for index in range(6)
        ]

        self.assertEqual(results, [True, True, True, True, True, False])

    def test_locked_device_does_not_persist_a_partial_account_row(self):
        for index in range(5):
            self.assertTrue(utils.check_password_reset_request_rate(
                self.request, f"device-user-{index}", now=self.now))
        account_scope = models.PasswordResetThrottle.Scope.REQUEST_ACCOUNT
        account_rows = models.PasswordResetThrottle.objects.filter(
            scope=account_scope).count()

        self.assertFalse(utils.check_password_reset_request_rate(
            self.request, "new-username", now=self.now))

        self.assertEqual(
            models.PasswordResetThrottle.objects.filter(
                scope=account_scope).count(),
            account_rows,
        )

    def test_eleventh_ip_request_is_rate_limited(self):
        results = [
            utils.check_password_reset_request_rate(
                self.make_request(ip_address="198.51.100.20"),
                f"ip-user-{index}",
                now=self.now,
            )
            for index in range(11)
        ]

        self.assertEqual(results, [True] * 10 + [False])

    def test_eleventh_account_verification_is_rate_limited(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        for index in range(10):
            request = self.make_request(
                ip_address=f"203.0.113.{index + 1}")
            self.assertFalse(utils.reset_password_from_token(
                request,
                self.user.username,
                f"{uuid.uuid4()}.invalid",
                "Secure-pass-123",
                now=self.now,
            ))

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))

    def test_eleventh_device_verification_is_rate_limited(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        for index in range(10):
            self.request.META["REMOTE_ADDR"] = f"198.51.100.{index + 1}"
            self.assertFalse(utils.reset_password_from_token(
                self.request,
                f"verification-user-{index}",
                f"{uuid.uuid4()}.invalid",
                "Secure-pass-123",
                now=self.now,
            ))
        self.request.META["REMOTE_ADDR"] = "192.0.2.10"

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_eleventh_ip_verification_is_rate_limited(self):
        token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        for index in range(10):
            self.assertFalse(utils.reset_password_from_token(
                self.make_request(),
                f"ip-verification-user-{index}",
                f"{uuid.uuid4()}.invalid",
                "Secure-pass-123",
                now=self.now,
            ))

        self.assertFalse(utils.reset_password_from_token(
            self.request,
            self.user.username,
            token,
            "Secure-pass-123",
            now=self.now,
        ))

    def test_cleanup_removes_only_expired_password_reset_state(self):
        expired_token = utils.create_password_reset_token(
            self.request, self.user, now=self.now - timedelta(days=2))
        active_token = utils.create_password_reset_token(
            self.request, self.user, now=self.now)
        expired_challenge_id = expired_token.split(".", 1)[0]
        active_challenge_id = active_token.split(".", 1)[0]
        stale_throttle = models.PasswordResetThrottle.objects.create(
            scope=models.PasswordResetThrottle.Scope.REQUEST_ACCOUNT,
            identifier_digest="a" * 64,
            window_started_at=self.now - timedelta(days=2),
        )
        active_throttle = models.PasswordResetThrottle.objects.create(
            scope=models.PasswordResetThrottle.Scope.REQUEST_ACCOUNT,
            identifier_digest="b" * 64,
            window_started_at=self.now,
        )

        utils.cleanup_password_reset_state(now=self.now)

        self.assertFalse(models.PasswordResetChallenge.objects.filter(
            pk=expired_challenge_id).exists())
        self.assertTrue(models.PasswordResetChallenge.objects.filter(
            pk=active_challenge_id).exists())
        self.assertFalse(models.PasswordResetThrottle.objects.filter(
            pk=stale_throttle.pk).exists())
        self.assertTrue(models.PasswordResetThrottle.objects.filter(
            pk=active_throttle.pk).exists())


class ForgetPasswordViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="view-reset-user",
            name="View Reset User",
            password="old-password",
        )
        models.NaturalPerson.objects.create(
            self.user,
            name="Reset",
            email="reset@example.com",
        )

    def send_email_token(self, username=None):
        prepared = {}

        def queue_delivery(prepare):
            prepared["args"] = prepare()
            return True

        with patch(
            "app.views.queue_prepared_password_reset_email",
            side_effect=queue_delivery,
        ):
            response = self.client.post(reverse("forgetpw"), {
                "action": "email",
                "username": username or self.user.username,
            })
        return response, prepared["args"][2]

    def test_post_requires_csrf(self):
        response = Client(enforce_csrf_checks=True).post(
            reverse("forgetpw"),
            {
                "username": "missing-user",
                "send_captcha": "email",
                "vertify_code": "",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_get_renders_reset_fields_without_writing_state(self):
        response = Client(enforce_csrf_checks=True).get(reverse("forgetpw"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="token"')
        self.assertContains(response, 'name="new_password"')
        self.assertContains(response, 'name="confirm_password"')
        self.assertNotContains(response, "验证码登录")
        self.assertFalse(models.PasswordResetChallenge.objects.exists())
        self.assertFalse(models.PasswordResetThrottle.objects.exists())

    def test_view_rejects_methods_other_than_get_and_post(self):
        response = self.client.put(reverse("forgetpw"))

        self.assertEqual(response.status_code, 405)

    @patch("app.views.queue_prepared_password_reset_email")
    def test_existing_and_missing_accounts_get_same_delivery_message(
        self, queue: Mock,
    ):
        prepared = []
        queue.side_effect = lambda prepare: prepared.append(prepare()) or True
        existing = self.client.post(reverse("forgetpw"), {
            "action": "email",
            "username": self.user.username,
        })
        missing = self.client.post(reverse("forgetpw"), {
            "action": "email",
            "username": "missing-user",
        })

        message = "若账号及联系方式有效，重置凭证将发送至已绑定渠道"
        self.assertContains(existing, message)
        self.assertContains(missing, message)
        self.assertEqual(queue.call_count, 2)
        self.assertIsNotNone(prepared[0])
        self.assertIsNone(prepared[1])

    @patch("app.views.queue_prepared_password_reset_email")
    def test_fourth_account_delivery_request_creates_no_challenge(
        self, queue: Mock,
    ):
        queue.side_effect = lambda prepare: prepare() is not None
        for _ in range(4):
            self.client.post(reverse("forgetpw"), {
                "action": "email",
                "username": self.user.username,
            })

        self.assertEqual(
            models.PasswordResetChallenge.objects.filter(
                user=self.user).count(),
            3,
        )

    def test_rejected_delivery_submission_preserves_existing_state(self):
        _, token = self.send_email_token()
        challenge = models.PasswordResetChallenge.objects.get(user=self.user)
        throttle_attempts = dict(
            models.PasswordResetThrottle.objects.values_list(
                "scope", "attempts"))

        with patch(
            "app.views.queue_prepared_password_reset_email",
            return_value=False,
        ) as queue:
            response = self.client.post(reverse("forgetpw"), {
                "action": "email",
                "username": self.user.username,
            })

        self.assertContains(
            response,
            "若账号及联系方式有效，重置凭证将发送至已绑定渠道",
        )
        queue.assert_called_once()
        challenge.refresh_from_db()
        self.assertIsNone(challenge.invalidated_at)
        self.assertEqual(
            models.PasswordResetChallenge.objects.filter(
                user=self.user).count(),
            1,
        )
        self.assertEqual(
            dict(models.PasswordResetThrottle.objects.values_list(
                "scope", "attempts")),
            throttle_attempts,
        )
        reset = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": "Secure-pass-123",
            "confirm_password": "Secure-pass-123",
        })
        self.assertRedirects(
            reset, reverse("index") + "?modinfo=success")

    def test_case_variant_request_and_reset_use_the_same_account(self):
        username = self.user.username.upper()
        _, token = self.send_email_token(username)

        response = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": username,
            "token": token,
            "new_password": "Secure-pass-123",
            "confirm_password": "Secure-pass-123",
        })

        self.assertRedirects(
            response, reverse("index") + "?modinfo=success")

    def test_full_account_takeover_regression_requires_normal_login(self):
        _, token = self.send_email_token()
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(
            self.client.get(reverse("welcome")).status_code,
            302,
        )

        reset = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": "Secure-pass-123",
            "confirm_password": "Secure-pass-123",
        })

        self.assertRedirects(
            reset, reverse("index") + "?modinfo=success")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotIn("forgetpw", self.client.session)
        self.assertEqual(
            self.client.get(reverse("welcome")).status_code,
            302,
        )

        replay = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": "Another-pass-123",
            "confirm_password": "Another-pass-123",
        })
        self.assertContains(replay, "重置凭证无效或已失效")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Secure-pass-123"))
        self.assertTrue(self.client.login(
            username=self.user.username,
            password="Secure-pass-123",
        ))

    def test_reset_rejects_password_matching_target_username(self):
        _, token = self.send_email_token()

        rejected = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": self.user.username,
            "confirm_password": self.user.username,
        })

        self.assertEqual(rejected.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("old-password"))
        self.assertIsNone(
            models.PasswordResetChallenge.objects.get(
                user=self.user).consumed_at)

        accepted = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": "Secure-pass-123",
            "confirm_password": "Secure-pass-123",
        })
        self.assertRedirects(
            accepted, reverse("index") + "?modinfo=success")

    def test_reset_preserves_password_whitespace(self):
        _, token = self.send_email_token()
        password = " Secure-pass-123 "

        response = self.client.post(reverse("forgetpw"), {
            "action": "reset",
            "username": self.user.username,
            "token": token,
            "new_password": password,
            "confirm_password": password,
        })

        self.assertRedirects(
            response, reverse("index") + "?modinfo=success")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))
        self.assertFalse(self.user.check_password(password.strip()))


class PasswordResetDeliveryTests(TestCase):
    @patch("extern.password_reset._delivery_executor.submit")
    @patch("extern.password_reset._delivery_slots.acquire", return_value=True)
    def test_email_delivery_is_queued_without_blocking_on_network(
        self,
        acquire: Mock,
        submit: Mock,
    ):
        token = "opaque-password-reset-token"

        self.assertTrue(password_reset.queue_password_reset_email(
            "Reset User",
            "reset@example.com",
            token,
        ))

        acquire.assert_called_once_with(blocking=False)
        submit.assert_called_once()
        delivery_runner, delivery, args = submit.call_args.args
        self.assertIs(delivery_runner, password_reset._run_delivery)
        self.assertIs(
            delivery,
            password_reset._deliver_password_reset_email,
        )
        self.assertEqual(args[-1], token)

    @patch("extern.password_reset._delivery_executor.submit")
    @patch("extern.password_reset._delivery_slots.acquire", return_value=True)
    def test_prepared_delivery_reserves_capacity_before_creating_token(
        self,
        acquire: Mock,
        submit: Mock,
    ):
        token = "opaque-password-reset-token"
        prepare = Mock(return_value=(
            "Reset User",
            "reset@example.com",
            token,
        ))

        self.assertTrue(
            password_reset.queue_prepared_password_reset_email(prepare))

        prepare.assert_called_once_with()
        delivery_runner, delivery, prepared_args = submit.call_args.args
        self.assertIs(
            delivery_runner,
            password_reset._run_prepared_delivery,
        )
        self.assertIs(
            delivery,
            password_reset._deliver_password_reset_email,
        )
        self.assertEqual(prepared_args.result()[-1], token)

    @patch("extern.password_reset._delivery_slots.acquire", return_value=False)
    def test_full_delivery_queue_skips_token_creation(self, acquire: Mock):
        prepare = Mock()

        self.assertFalse(
            password_reset.queue_prepared_password_reset_email(prepare))

        acquire.assert_called_once_with(blocking=False)
        prepare.assert_not_called()

    @patch("extern.password_reset._delivery_slots.release")
    @patch(
        "extern.password_reset._delivery_executor.submit",
        side_effect=RuntimeError,
    )
    @patch("extern.password_reset._delivery_slots.acquire", return_value=True)
    def test_unavailable_delivery_executor_skips_token_creation(
        self,
        acquire: Mock,
        submit: Mock,
        release: Mock,
    ):
        prepare = Mock()

        self.assertFalse(
            password_reset.queue_prepared_password_reset_email(prepare))

        prepare.assert_not_called()
        release.assert_called_once_with()

    @patch("extern.password_reset.requests.post")
    def test_email_delivery_contains_the_opaque_token(self, post: Mock):
        token = "opaque-password-reset-token"
        post.return_value.json.return_value = {"status": 200}

        password_reset._deliver_password_reset_email(
            "Reset User",
            "reset@example.com",
            token,
        )

        post.assert_called_once()
        post_data = json.loads(post.call_args.args[1])
        self.assertEqual(post_data["toaddrs"], ["reset@example.com"])
        self.assertIn(token, post_data["content"])
        self.assertEqual(post.call_args.kwargs["timeout"], 6)
        post.return_value.raise_for_status.assert_called_once_with()

    @patch("extern.password_reset.requests.post")
    def test_email_delivery_rejects_http_errors(self, post: Mock):
        post.return_value.raise_for_status.side_effect = requests.HTTPError

        with self.assertRaises(requests.HTTPError):
            password_reset._deliver_password_reset_email(
                "Reset User",
                "reset@example.com",
                "opaque-password-reset-token",
            )

    @patch("extern.password_reset.requests.post")
    def test_email_delivery_rejects_application_errors(self, post: Mock):
        post.return_value.json.return_value = {"status": 500}

        with self.assertRaisesRegex(
            RuntimeError,
            "email service rejected delivery",
        ):
            password_reset._deliver_password_reset_email(
                "Reset User",
                "reset@example.com",
                "opaque-password-reset-token",
            )

    @patch("extern.wechat.send_wechat")
    def test_wechat_token_is_not_persisted_in_a_scheduler_job(
        self, send_wechat: Mock,
    ):
        token = "opaque-password-reset-token"

        send_password_reset_token("1234567890", token)

        send_wechat.assert_called_once()
        args, kwargs = send_wechat.call_args
        self.assertIn(token, args[2])
        self.assertEqual(args[1], "YPPF密码重置")
        self.assertFalse(kwargs["multithread"])
