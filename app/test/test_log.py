import logging
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase

from app.log import ProfileLogger


class ProfileLoggerRedactionTestCase(SimpleTestCase):
    def test_secure_view_omits_post_and_query_values_from_alerts(self):
        post_secrets = ["password-v13", "token-v13", "identity-v13"]
        query_secret = "auth-v13-query-secret"
        request = RequestFactory().post(
            f"/failing-view/?auth={query_secret}",
            {
                "password": post_secrets[0],
                "token": post_secrets[1],
                "identity": post_secrets[2],
            },
        )
        request.user = AnonymousUser()
        logger = ProfileLogger("v13-test")
        logger.setLevel(logging.DEBUG)
        logger.set_debug_mode(False)

        @logger.secure_view()
        def failing_view(request):
            raise RuntimeError("diagnostic-v13")

        with patch.object(logger, "_send_wechat") as send_wechat:
            with self.assertLogs(logger, level="ERROR") as captured:
                response = failing_view(request)

        local_message = "\n".join(captured.output)
        wechat_message = send_wechat.call_args.args[0]
        self.assertEqual(response.status_code, 302)
        for message in (local_message, wechat_message):
            self.assertIn("URL: /failing-view/", message)
            self.assertNotIn("auth=", message)
            self.assertIn("Method: POST", message)
            for value in (*post_secrets, query_secret):
                self.assertNotIn(value, message)
