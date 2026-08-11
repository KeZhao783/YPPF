from unittest.mock import patch

from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from dormitory.models import Agreement, Dormitory, DormitoryAssignment
from dormitory.views import (
    DormitoryAgreementViewSet,
    DormitoryAssignmentViewSet,
    DormitoryRoutineQAView,
)
from generic.models import User
from questionnaire.models import AnswerSheet, Choice, Question, Survey


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


class DormitoryReadApiSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_a = User.objects.create_user(
            username="security_student_a",
            name="Security Student A",
            password="test-password",
            utype=User.Type.STUDENT,
        )
        cls.user_b = User.objects.create_user(
            username="security_student_b",
            name="Security Student B",
            password="test-password",
            utype=User.Type.STUDENT,
        )
        cls.staff_user = User.objects.create_user(
            username="security_staff",
            name="Security Staff",
            password="test-password",
            utype=User.Type.STUDENT,
            is_staff=True,
        )
        cls.inactive_user = User.objects.create_user(
            username="security_inactive",
            name="Security Inactive",
            password="test-password",
            utype=User.Type.STUDENT,
            active=False,
        )
        cls.organization_user = User.objects.create_user(
            username="security_organization",
            name="Security Organization",
            password="test-password",
            utype=User.Type.ORG,
        )
        User.objects.create_user(
            username="zz00000",
            name="Synthetic Official User",
            password="test-password",
        )
        dormitory_a = Dormitory.objects.create(
            id=99001,
            capacity=4,
            gender=Dormitory.Gender.FEMALE,
        )
        dormitory_b = Dormitory.objects.create(
            id=99002,
            capacity=4,
            gender=Dormitory.Gender.MALE,
        )
        cls.assignment_a = DormitoryAssignment.objects.create(
            dormitory=dormitory_a,
            user=cls.user_a,
            bed_id=1,
        )
        cls.assignment_b = DormitoryAssignment.objects.create(
            dormitory=dormitory_b,
            user=cls.user_b,
            bed_id=2,
        )
        cls.agreement_a = Agreement.objects.create(user=cls.user_a)
        cls.agreement_b = Agreement.objects.create(user=cls.user_b)
        cls.staff_assignment = DormitoryAssignment.objects.create(
            dormitory=dormitory_a,
            user=cls.staff_user,
            bed_id=3,
        )
        cls.inactive_assignment = DormitoryAssignment.objects.create(
            dormitory=dormitory_a,
            user=cls.inactive_user,
            bed_id=4,
        )
        cls.organization_assignment = DormitoryAssignment.objects.create(
            dormitory=dormitory_b,
            user=cls.organization_user,
            bed_id=3,
        )
        cls.inactive_assignment_a = DormitoryAssignment.objects.create(
            dormitory=dormitory_b,
            user=cls.user_a,
            bed_id=4,
            active=False,
        )
        cls.staff_agreement = Agreement.objects.create(user=cls.staff_user)
        cls.inactive_agreement = Agreement.objects.create(
            user=cls.inactive_user)
        cls.organization_agreement = Agreement.objects.create(
            user=cls.organization_user)

    def setUp(self):
        self.client = APIClient()

    def test_anonymous_user_cannot_read_dormitory_assignments(self):
        list_response = self.client.get(reverse("dormitoryassignment-list"))
        detail_response = self.client.get(reverse(
            "dormitoryassignment-detail", args=[self.assignment_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(detail_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_user_cannot_read_agreements(self):
        list_response = self.client.get(reverse("agreement-query-list"))
        detail_response = self.client.get(reverse(
            "agreement-query-detail", args=[self.agreement_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(detail_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_user_cannot_read_legacy_agreement_status(self):
        list_response = self.client.get(
            reverse("agreement-query-fixme-list"))
        detail_response = self.client.get(reverse(
            "agreement-query-fixme-detail", args=[self.agreement_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(detail_response.status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_authenticated_user_reads_only_own_dormitory_assignment(self):
        self.client.force_login(self.user_a)

        list_response = self.client.get(reverse("dormitoryassignment-list"))
        other_detail_response = self.client.get(reverse(
            "dormitoryassignment-detail", args=[self.assignment_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [assignment["id"] for assignment in list_response.data],
            [self.assignment_a.pk],
        )
        self.assertEqual(
            set(list_response.data[0]),
            {"id", "dormitory", "bed_id"},
        )
        self.assertEqual(
            other_detail_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_authenticated_user_reads_only_own_agreement(self):
        self.client.force_login(self.user_a)

        list_response = self.client.get(reverse("agreement-query-list"))
        other_detail_response = self.client.get(reverse(
            "agreement-query-detail", args=[self.agreement_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]["id"], self.agreement_a.pk)
        self.assertEqual(
            set(list_response.data[0]),
            {"id", "sign_time"},
        )
        self.assertEqual(
            other_detail_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_staff_user_has_no_full_table_bypass(self):
        self.client.force_login(self.staff_user)

        assignment_response = self.client.get(
            reverse("dormitoryassignment-list"))
        agreement_response = self.client.get(reverse("agreement-query-list"))

        self.assertEqual(
            [assignment["id"] for assignment in assignment_response.data],
            [self.staff_assignment.pk],
        )
        self.assertEqual(
            [agreement["id"] for agreement in agreement_response.data],
            [self.staff_agreement.pk],
        )

    def test_inactive_and_organization_users_receive_empty_lists(self):
        cases = [
            (
                self.inactive_user,
                self.inactive_assignment,
                self.inactive_agreement,
            ),
            (
                self.organization_user,
                self.organization_assignment,
                self.organization_agreement,
            ),
        ]
        for user, assignment, agreement in cases:
            self.client.force_login(user)

            assignment_response = self.client.get(
                reverse("dormitoryassignment-list"))
            agreement_response = self.client.get(
                reverse("agreement-query-list"))
            assignment_detail_response = self.client.get(reverse(
                "dormitoryassignment-detail", args=[assignment.pk]))
            agreement_detail_response = self.client.get(reverse(
                "agreement-query-detail", args=[agreement.pk]))

            self.assertEqual(
                assignment_response.status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(
                agreement_response.status_code,
                status.HTTP_200_OK,
            )
            self.assertEqual(assignment_response.data, [])
            self.assertEqual(agreement_response.data, [])
            self.assertEqual(
                assignment_detail_response.status_code,
                status.HTTP_404_NOT_FOUND,
            )
            self.assertEqual(
                agreement_detail_response.status_code,
                status.HTTP_404_NOT_FOUND,
            )

            self.client.logout()

    def test_legacy_agreement_status_get_does_not_write_database(self):
        self.client.force_login(self.organization_user)
        agreement_count = Agreement.objects.count()

        response = self.client.get(reverse("agreement-query-fixme-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [{"id": 0}])
        self.assertEqual(Agreement.objects.count(), agreement_count)

    def test_sensitive_viewsets_have_no_class_level_querysets(self):
        self.assertNotIn("queryset", DormitoryAssignmentViewSet.__dict__)
        self.assertNotIn("queryset", DormitoryAgreementViewSet.__dict__)

    def test_active_student_reads_only_own_legacy_agreement_status(self):
        self.client.force_login(self.user_a)

        list_response = self.client.get(
            reverse("agreement-query-fixme-list"))
        other_detail_response = self.client.get(reverse(
            "agreement-query-fixme-detail", args=[self.agreement_b.pk]))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [agreement["id"] for agreement in list_response.data],
            [self.agreement_a.pk],
        )
        self.assertEqual(other_detail_response.status_code,
                         status.HTTP_404_NOT_FOUND)
