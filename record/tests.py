from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase

from record.log.logger import Logger


class RequestLoggingTestCase(SimpleTestCase):
    def test_format_request_omits_post_values(self):
        secrets = ["pw-v13-secret", "token-v13-secret", "code-v13-secret"]
        request = RequestFactory().post(
            "/submit-sensitive/?source=test",
            {"password": secrets[0], "token": secrets[1], "code": secrets[2]},
        )
        request.user = AnonymousUser()

        message = Logger.format_request(request)

        self.assertIn("URL: /submit-sensitive/?source=test", message)
        self.assertIn("Method: POST", message)
        self.assertNotIn("Data:", message)
        for value in secrets:
            self.assertNotIn(value, message)
