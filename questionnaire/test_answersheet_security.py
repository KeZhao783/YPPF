from datetime import datetime, timedelta

from django.urls import reverse
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from generic.models import User
from questionnaire.models import AnswerSheet, Survey


class AnswerSheetApiSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.asker = User.objects.create_user(
            username="v10_asker",
            name="V10 Asker",
            password="test-password",
        )
        cls.respondent = User.objects.create_user(
            username="v10_respondent",
            name="V10 Respondent",
            password="test-password",
        )
        cls.unrelated = User.objects.create_user(
            username="v10_unrelated",
            name="V10 Unrelated",
            password="test-password",
        )
        cls.staff = User.objects.create_user(
            username="v10_staff",
            name="V10 Staff",
            password="test-password",
            is_staff=True,
        )
        now = datetime.now()
        cls.survey = Survey.objects.create(
            title="V10 API boundary survey",
            creator=cls.asker,
            status=Survey.Status.PUBLISHED,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        cls.draft = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
        )
        cls.put_draft = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
        )
        cls.submitted = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
            status=AnswerSheet.Status.SUBMITTED,
        )

    def setUp(self):
        self.client = APIClient()

    def test_generic_put_and_patch_are_disabled(self):
        self.client.force_login(self.respondent)

        patch_response = self.client.patch(
            reverse("answersheet-detail", args=[self.draft.pk]),
            {
                "survey": self.survey.pk,
                "status": AnswerSheet.Status.SUBMITTED,
            },
            format="json",
        )
        put_response = self.client.put(
            reverse("answersheet-detail", args=[self.put_draft.pk]),
            {
                "survey": self.survey.pk,
                "status": AnswerSheet.Status.SUBMITTED,
            },
            format="json",
        )

        self.assertEqual(
            patch_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            put_response.status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.draft.refresh_from_db()
        self.put_draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)
        self.assertEqual(self.put_draft.status, AnswerSheet.Status.DRAFT)

    def test_create_ignores_client_status_and_fixes_creator(self):
        self.client.force_login(self.unrelated)

        response = self.client.post(
            reverse("answersheet-list"),
            {
                "survey": self.survey.pk,
                "creator": self.respondent.pk,
                "status": AnswerSheet.Status.SUBMITTED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = AnswerSheet.objects.get(pk=response.data["id"])
        self.assertEqual(created.creator, self.unrelated)
        self.assertEqual(created.status, AnswerSheet.Status.DRAFT)

    def test_survey_owner_lists_only_submitted_sheets(self):
        self.client.force_login(self.asker)

        response = self.client.get(reverse("answersheet-survey-owner"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in response.data],
            [self.submitted.pk],
        )

    def test_survey_owner_can_retrieve_only_submitted_sheet(self):
        self.client.force_login(self.asker)

        submitted_response = self.client.get(
            reverse("answersheet-detail", args=[self.submitted.pk]))
        draft_response = self.client.get(
            reverse("answersheet-detail", args=[self.draft.pk]))

        self.assertEqual(
            submitted_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            draft_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_staff_has_no_answer_sheet_read_bypass(self):
        self.client.force_login(self.staff)

        response = self.client.get(
            reverse("answersheet-detail", args=[self.submitted.pk]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
