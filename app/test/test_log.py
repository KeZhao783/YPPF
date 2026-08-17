import logging
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase

from app.log import ProfileLogger


class ProfileLoggerRedactionTestCase(SimpleTestCase):
    def test_secure_view_omits_post_values_from_log_and_wechat(self):
        secrets = ["password-v13", "token-v13", "identity-v13"]
        request = RequestFactory().post(
            "/failing-view/",
            {"password": secrets[0], "token": secrets[1], "identity": secrets[2]},
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
            self.assertIn("Method: POST", message)
            for value in secrets:
                self.assertNotIn(value, message)
