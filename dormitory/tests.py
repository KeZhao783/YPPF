from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from dormitory.views import DormitoryRoutineQAView
from generic.models import User
from questionnaire.models import (
    AnswerSheet,
    AnswerText,
    Choice,
    Question,
    Survey,
)


class CreateDormitoryQuestionnaire2026Tests(TestCase):
    def test_creation_is_rolled_back_if_a_database_operation_fails(self):
        User.objects.create_user(username="creator", name="Creator", id=1)
        original_create = Question.objects.create

        def fail_on_second_question(**kwargs):
            if kwargs["order"] == 2:
                raise RuntimeError("simulated database operation failure")
            return original_create(**kwargs)

        with patch.object(
            Question.objects, "create", side_effect=fail_on_second_question
        ):
            with self.assertRaises(RuntimeError):
                call_command("create_dormitory_questionnaire_2026")

        self.assertFalse(
            Survey.objects.filter(title="宿舍生活习惯调研-2026").exists()
        )


class DormitoryRoutineQAValidationTests(TestCase):
    def test_invalid_choice_is_rejected_before_answer_sheet_creation(self):
        user = User.objects.create_user(username="student", name="Student")
        survey = Survey.objects.create(
            title="Dormitory survey",
            creator=user,
            start_time="2026-08-08",
            end_time="2026-08-14",
        )
        question = Question.objects.create(
            survey=survey,
            order=1,
            topic="Choice",
            type=Question.Type.SINGLE,
        )
        Choice.objects.create(question=question, order=1, text="Valid")

        view = DormitoryRoutineQAView()
        view.request = RequestFactory().post("/dormitory/routine-QA/", {"1": "2"})
        view.request.user = user
        view.get_survey = lambda: survey
        response = object()
        view.render = lambda **kwargs: response

        self.assertIs(view.post(), response)
        self.assertFalse(AnswerSheet.objects.filter(survey=survey).exists())

    def test_valid_response_is_submitted_and_visible_to_survey_creator(self):
        creator = User.objects.create_user(
            username="dormitory_creator",
            name="Dormitory Creator",
        )
        student = User.objects.create_user(
            username="dormitory_student",
            name="Dormitory Student",
        )
        now = datetime.now()
        survey = Survey.objects.create(
            title="Dormitory published survey",
            creator=creator,
            status=Survey.Status.PUBLISHED,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        question = Question.objects.create(
            survey=survey,
            order=1,
            topic="Required text",
            type=Question.Type.TEXT,
            required=True,
        )

        view = DormitoryRoutineQAView()
        view.request = RequestFactory().post(
            "/dormitory/routine-QA/",
            {"1": "valid response"},
        )
        view.request.user = student
        view.get_survey = lambda: survey
        response = object()
        view.render = lambda **kwargs: response

        self.assertIs(view.post(), response)

        sheet = AnswerSheet.objects.get(survey=survey, creator=student)
        self.assertEqual(sheet.status, AnswerSheet.Status.SUBMITTED)
        self.assertEqual(
            AnswerText.objects.get(
                answersheet=sheet,
                question=question,
            ).body,
            "valid response",
        )

        client = APIClient()
        client.force_login(creator)
        result_response = client.get(reverse("answersheet-survey-owner"))

        self.assertEqual(result_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in result_response.data],
            [sheet.pk],
        )
