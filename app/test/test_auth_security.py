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
