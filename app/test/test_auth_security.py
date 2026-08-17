from urllib.parse import urlencode

from django.test import TestCase

from app.models import NaturalPerson
from generic.models import User
from utils.hasher import MyMD5Hasher


class LegacyMiniLoginRemovalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "valid-password"
        cls.user = User.objects.create_user(
            "v14-user", "V14 User", User.Type.PERSON,
            password=cls.password, is_newuser=False,
        )
        NaturalPerson.objects.create(cls.user, name="V14 User")

    def test_old_predictable_token_cannot_create_session(self):
        token = MyMD5Hasher("wechat_login").encode(self.user.username)
        payload = {
            "username": self.user.username,
            "password": self.password,
            "secret_token": token,
        }
        for path in ("/minilogin", "/yppf/minilogin"):
            with self.subTest(path=path):
                self.client.logout()
                response = self.client.post(path, payload)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_redirects_only_to_safe_local_target(self):
        cases = (
            ("/inside?x=1", "/inside?x=1"),
            ("http://testserver/inside", "http://testserver/inside"),
            ("//evil.example/phish", "/welcome/"),
            ("/\\evil.example/phish", "/welcome/"),
            ("https://evil.example/phish", "/welcome/"),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                self.client.logout()
                query = urlencode({"origin": target})
                response = self.client.post(
                    f"/login/?{query}",
                    {"username": self.user.username, "password": self.password},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], expected)

    def test_account_switch_redirects_only_to_safe_local_target(self):
        cases = (
            ("/inside", "/inside"),
            ("http://testserver/inside", "http://testserver/inside"),
            ("//evil.example/phish", "/welcome/"),
            ("/\\evil.example/phish", "/welcome/"),
            ("https://evil.example/phish", "/welcome/"),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                self.client.force_login(self.user)
                session = self.client.session
                session["NP"] = self.user.username
                session.save()
                query = urlencode({"origin": target})
                response = self.client.get(f"/shiftAccount/?{query}")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], expected)
