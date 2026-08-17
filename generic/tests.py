from urllib.parse import urlencode
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase

from generic.models import User
from utils.http.utils import safe_local_redirect_target


class SafeLocalRedirectTargetTestCase(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/", HTTP_HOST="testserver")

    def test_accepts_local_paths_and_same_host_absolute_urls(self):
        for target in (
            "/inside?x=1", "http://testserver/inside", "https://testserver/inside"
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    safe_local_redirect_target(self.request, target, "/fallback/"),
                    target,
                )

    def test_rejects_unsafe_or_ambiguous_targets(self):
        targets = (
            "https://evil.example/phish", "//evil.example/phish",
            "//testserver/phish", "/\\evil.example", "\\evil.example",
            "javascript:alert(1)", "inside", "", "   ", None,
        )
        for target in targets:
            with self.subTest(target=target):
                self.assertEqual(
                    safe_local_redirect_target(self.request, target, "/fallback/"),
                    "/fallback/",
                )

    def test_rejects_http_target_for_secure_request(self):
        request = RequestFactory().get(
            "/", HTTP_HOST="testserver", secure=True
        )

        self.assertEqual(
            safe_local_redirect_target(
                request, "http://testserver/inside", "/fallback/"
            ),
            "/fallback/",
        )


class WebviewRedirectSafetyTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "webview-v15", "Webview V15", User.Type.PERSON,
            password="pw", is_newuser=False,
        )

    def test_webview_redirects_only_to_safe_local_target(self):
        cases = (
            ("/inside?x=1", "/inside?x=1"),
            ("http://testserver/inside", "http://testserver/inside"),
            ("//evil.example/phish", "/"),
            ("/\\evil.example/phish", "/"),
            ("https://evil.example/phish", "/"),
            ("javascript:alert(1)", "/"),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                self.client.logout()
                query = urlencode({"ticket": "fresh", "to": target})
                with patch(
                    "generic.views.TicketAuthentication.authenticate",
                    return_value=(self.user, None),
                ):
                    response = self.client.get(f"/redirect/?{query}")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], expected)
                self.assertEqual(
                    int(self.client.session["_auth_user_id"]), self.user.pk
                )
