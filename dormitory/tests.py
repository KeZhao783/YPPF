from datetime import datetime, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from dormitory.management.commands.assign_dormitory import Dormitory, Freshman
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
    def assert_submission_rejection_preserves_input(
        self,
        *,
        survey_status,
        start_time,
        end_time,
        expected_warning,
    ):
        creator = User.objects.create_user(
            username="dormitory_rejection_creator",
            name="Dormitory Rejection Creator",
        )
        student = User.objects.create_user(
            username="dormitory_rejection_student",
            name="Dormitory Rejection Student",
        )
        survey = Survey.objects.create(
            title="Dormitory rejected survey",
            creator=creator,
            status=survey_status,
            start_time=start_time,
            end_time=end_time,
        )
        question = Question.objects.create(
            survey=survey,
            order=1,
            topic="Required text",
            type=Question.Type.TEXT,
            required=True,
        )
        submitted_value = "preserve this response"

        view = DormitoryRoutineQAView()
        view.request = RequestFactory().post(
            "/dormitory/routine-QA/",
            {"1": submitted_value},
        )
        view.request.user = student
        view.get_survey = lambda: survey
        response = object()
        rendered = {}

        def capture_render(**kwargs):
            rendered.update(kwargs)
            return response

        view.render = capture_render

        self.assertIs(view.post(), response)
        self.assertEqual(
            rendered["html_display"],
            {"warn_code": 1, "warn_message": expected_warning},
        )
        rendered_question, _, rendered_value = rendered["survey_iter"][0]
        self.assertEqual(rendered_question, question)
        self.assertEqual(rendered_value, submitted_value)
        self.assertFalse(
            AnswerSheet.objects.filter(survey=survey, creator=student).exists()
        )
        self.assertFalse(AnswerText.objects.filter(question=question).exists())

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

    def test_unpublished_survey_rejection_preserves_entered_values(self):
        now = datetime.now()
        self.assert_submission_rejection_preserves_input(
            survey_status=Survey.Status.ENDED,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
            expected_warning="只能提交已发布的问卷！",
        )

    def test_expired_survey_rejection_preserves_entered_values(self):
        now = datetime.now()
        self.assert_submission_rejection_preserves_input(
            survey_status=Survey.Status.PUBLISHED,
            start_time=now - timedelta(days=2),
            end_time=now - timedelta(days=1),
            expected_warning="当前不在问卷提交时间内！",
        )

class DormitoryMajorPreferenceScoringTests(TestCase):
    @staticmethod
    def make_dorm(majors, preferences):
        dorm = Dormitory(101, 4, False)
        for major, preference in zip(majors, preferences):
            dorm.add(Freshman({
                "major": major,
                "major_composition_preference": preference,
                "origin": "省份",
                "personality": 1,
                "olympiad": 0,
                "ac_temp": 26,
                "all_night_ac": 1,
                "wake": 1,
                "sleep": 1,
                "sleep_quality": 1,
                "environment": 0,
                "expectation": 0,
            }))
        return dorm

    def test_major_composition_has_no_room_level_score_without_preferences(self):
        diverse = self.make_dorm([0, 1, 2, 3], ["either"] * 4)
        uneven = self.make_dorm([0, 0, 0, 1], ["either"] * 4)
        same = self.make_dorm([0, 0, 0, 0], ["either"] * 4)

        self.assertEqual(diverse.check_better(), uneven.check_better())
        self.assertEqual(diverse.check_better(), same.check_better())

    def test_similar_preference_rewards_same_major_roommates(self):
        same = self.make_dorm([0, 0, 0, 0], ["similar"] * 4)
        mixed = self.make_dorm([0, 1, 2, 3], ["similar"] * 4)

        self.assertGreater(same.check_better(), mixed.check_better())

    def test_mixed_preference_rewards_cross_discipline_roommates(self):
        same = self.make_dorm([0, 0, 0, 0], ["mixed"] * 4)
        mixed = self.make_dorm([0, 1, 2, 3], ["mixed"] * 4)

        self.assertGreater(mixed.check_better(), same.check_better())

    def test_roommate_personality_preference_rewards_matching_roommates(self):
        matching = self.make_dorm([0, 1, 2, 3], ["either"] * 4)
        mismatching = self.make_dorm([0, 1, 2, 3], ["either"] * 4)
        for student in matching.stu:
            student.data.update(
                personality=2,
                roommate_personality_preference=2,
            )
        for student in mismatching.stu:
            student.data.update(
                personality=0,
                roommate_personality_preference=2,
            )

        # Account for the pre-existing penalty for rooms with >2 introverts.
        self.assertGreater(
            matching.check_better(),
            mismatching.check_better() + 600,
        )

    def test_roommate_expectation_rewards_matching_roommates(self):
        matching = self.make_dorm([0, 1, 2, 3], ["either"] * 4)
        mismatching = self.make_dorm([0, 1, 2, 3], ["either"] * 4)
        for student in matching.stu:
            student.data.update(expectation=1, roommate_expectation=1)
        for student in mismatching.stu:
            student.data.update(expectation=0, roommate_expectation=1)

        self.assertGreater(matching.check_better(), mismatching.check_better())
