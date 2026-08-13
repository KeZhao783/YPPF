from datetime import datetime, timedelta
from threading import Event, Thread

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from generic.models import User
from questionnaire.models import (
    AnswerSheet,
    AnswerText,
    Choice,
    Question,
    Survey,
)
from questionnaire.utils import submit_answersheet


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


class AnswerSheetSubmitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.asker = User.objects.create_user(
            username="v10_submit_asker",
            name="V10 Submit Asker",
            password="test-password",
        )
        cls.respondent = User.objects.create_user(
            username="v10_submit_respondent",
            name="V10 Submit Respondent",
            password="test-password",
        )
        cls.unrelated = User.objects.create_user(
            username="v10_submit_unrelated",
            name="V10 Submit Unrelated",
            password="test-password",
        )
        cls.staff = User.objects.create_user(
            username="v10_submit_staff",
            name="V10 Submit Staff",
            password="test-password",
            is_staff=True,
        )
        cls.now = datetime.now()
        cls.survey = Survey.objects.create(
            title="V10 submit survey",
            creator=cls.asker,
            status=Survey.Status.PUBLISHED,
            start_time=cls.now - timedelta(days=1),
            end_time=cls.now + timedelta(days=1),
        )
        cls.required_question = Question.objects.create(
            survey=cls.survey,
            order=1,
            topic="Required text",
            type=Question.Type.TEXT,
            required=True,
        )
        cls.choice_question = Question.objects.create(
            survey=cls.survey,
            order=2,
            topic="Optional choice",
            type=Question.Type.SINGLE,
            required=False,
        )
        Choice.objects.create(
            question=cls.choice_question,
            order=1,
            text="Valid",
        )
        cls.draft = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
        )
        cls.required_answer = AnswerText.objects.create(
            question=cls.required_question,
            answersheet=cls.draft,
            body="complete",
        )
        cls.submitted = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
            status=AnswerSheet.Status.SUBMITTED,
        )
        AnswerText.objects.create(
            question=cls.required_question,
            answersheet=cls.submitted,
            body="already submitted",
        )

    def setUp(self):
        self.client = APIClient()

    def _submit(self, sheet=None):
        target = self.draft if sheet is None else sheet
        return self.client.post(
            f"/questionnaire/answersheet/{target.pk}/submit/",
            {},
            format="json",
        )

    def _new_draft(self, survey=None):
        target_survey = self.survey if survey is None else survey
        return AnswerSheet.objects.create(
            survey=target_survey,
            creator=self.respondent,
        )

    def test_owner_submits_complete_draft(self):
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.SUBMITTED)
        self.assertEqual(response.data["status"], AnswerSheet.Status.SUBMITTED)

    def test_nonowners_cannot_submit_respondent_draft(self):
        for actor in (self.asker, self.unrelated, self.staff):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)

                response = self._submit()

                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.draft.refresh_from_db()
                self.assertEqual(
                    self.draft.status,
                    AnswerSheet.Status.DRAFT,
                )

    def test_anonymous_user_cannot_submit(self):
        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)

    def test_session_submit_requires_csrf(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        logged_in = csrf_client.login(
            username=self.respondent.username,
            password="test-password",
        )
        self.assertTrue(logged_in)

        response = csrf_client.post(
            f"/questionnaire/answersheet/{self.draft.pk}/submit/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)

    def test_repeated_submit_is_rejected(self):
        self.client.force_login(self.respondent)

        response = self._submit(self.submitted)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.submitted.refresh_from_db()
        self.assertEqual(
            self.submitted.status,
            AnswerSheet.Status.SUBMITTED,
        )

    def test_missing_required_answer_does_not_submit(self):
        self.required_answer.delete()
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)
        self.assertFalse(
            AnswerText.objects.filter(answersheet=self.draft).exists())

    def test_survey_state_and_time_window_are_enforced(self):
        cases = [
            (
                "not-published",
                Survey.Status.REVIEWING,
                self.now - timedelta(days=1),
                self.now + timedelta(days=1),
            ),
            (
                "not-started",
                Survey.Status.PUBLISHED,
                self.now + timedelta(days=1),
                self.now + timedelta(days=2),
            ),
            (
                "expired",
                Survey.Status.PUBLISHED,
                self.now - timedelta(days=2),
                self.now - timedelta(days=1),
            ),
        ]
        self.client.force_login(self.respondent)
        for name, survey_status, start_time, end_time in cases:
            with self.subTest(case=name):
                survey = Survey.objects.create(
                    title=f"V10 submit {name}",
                    creator=self.asker,
                    status=survey_status,
                    start_time=start_time,
                    end_time=end_time,
                )
                question = Question.objects.create(
                    survey=survey,
                    order=1,
                    topic=f"Required {name}",
                    type=Question.Type.TEXT,
                    required=True,
                )
                sheet = self._new_draft(survey)
                AnswerText.objects.create(
                    question=question,
                    answersheet=sheet,
                    body="complete",
                )

                response = self._submit(sheet)

                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                sheet.refresh_from_db()
                self.assertEqual(sheet.status, AnswerSheet.Status.DRAFT)

    def test_duplicate_answers_do_not_submit(self):
        AnswerText.objects.create(
            question=self.required_question,
            answersheet=self.draft,
            body="duplicate",
        )
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)
        self.assertEqual(
            AnswerText.objects.filter(answersheet=self.draft).count(),
            2,
        )

    def test_cross_survey_answer_does_not_submit(self):
        other_survey = Survey.objects.create(
            title="V10 other survey",
            creator=self.asker,
            status=Survey.Status.PUBLISHED,
            start_time=self.now - timedelta(days=1),
            end_time=self.now + timedelta(days=1),
        )
        other_question = Question.objects.create(
            survey=other_survey,
            order=1,
            topic="Other question",
            type=Question.Type.TEXT,
        )
        AnswerText.objects.create(
            question=other_question,
            answersheet=self.draft,
            body="cross survey",
        )
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)

    def test_empty_stored_answer_does_not_submit(self):
        self.required_answer.body = ""
        self.required_answer.save(update_fields=["body"])
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft.refresh_from_db()
        self.required_answer.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)
        self.assertEqual(self.required_answer.body, "")

    def test_invalid_choice_answer_does_not_submit(self):
        AnswerText.objects.create(
            question=self.choice_question,
            answersheet=self.draft,
            body="99",
        )
        self.client.force_login(self.respondent)

        response = self._submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)

    def test_submit_reuses_prefetched_choices(self):
        second_choice_question = Question.objects.create(
            survey=self.survey,
            order=3,
            topic="Second optional choice",
            type=Question.Type.SINGLE,
            required=False,
        )
        Choice.objects.create(
            question=second_choice_question,
            order=1,
            text="Second valid choice",
        )
        AnswerText.objects.create(
            question=self.choice_question,
            answersheet=self.draft,
            body="1",
        )
        AnswerText.objects.create(
            question=second_choice_question,
            answersheet=self.draft,
            body="1",
        )

        with CaptureQueriesContext(connection) as queries:
            submit_answersheet(self.draft.pk, self.respondent, now=self.now)

        choice_queries = [
            query["sql"]
            for query in queries.captured_queries
            if "questionnaire_choice" in query["sql"].lower()
        ]
        self.assertEqual(len(choice_queries), 1, choice_queries)


class AnswerTextSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.asker = User.objects.create_user(
            username="v10_text_asker",
            name="V10 Text Asker",
            password="test-password",
        )
        cls.respondent = User.objects.create_user(
            username="v10_text_respondent",
            name="V10 Text Respondent",
            password="test-password",
        )
        cls.unrelated = User.objects.create_user(
            username="v10_text_unrelated",
            name="V10 Text Unrelated",
            password="test-password",
        )
        cls.staff = User.objects.create_user(
            username="v10_text_staff",
            name="V10 Text Staff",
            password="test-password",
            is_staff=True,
        )
        now = datetime.now()
        cls.survey = Survey.objects.create(
            title="V10 answer text survey",
            creator=cls.asker,
            status=Survey.Status.PUBLISHED,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        cls.primary_question = Question.objects.create(
            survey=cls.survey,
            order=1,
            topic="Primary text",
            type=Question.Type.TEXT,
        )
        cls.optional_question = Question.objects.create(
            survey=cls.survey,
            order=2,
            topic="Optional text",
            type=Question.Type.TEXT,
            required=False,
        )
        cls.draft = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
        )
        cls.submitted = AnswerSheet.objects.create(
            survey=cls.survey,
            creator=cls.respondent,
            status=AnswerSheet.Status.SUBMITTED,
        )
        cls.draft_answer = AnswerText.objects.create(
            question=cls.primary_question,
            answersheet=cls.draft,
            body="draft body",
        )
        cls.submitted_answer = AnswerText.objects.create(
            question=cls.primary_question,
            answersheet=cls.submitted,
            body="submitted body",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.raise_request_exception = False

    def test_owner_can_create_sparse_update_and_delete_answer_in_draft(self):
        self.client.force_login(self.respondent)

        create_response = self.client.post(
            reverse("answertext-list"),
            {
                "question": self.optional_question.pk,
                "answersheet": self.draft.pk,
                "body": "optional body",
            },
            format="json",
        )
        self.assertEqual(
            create_response.status_code,
            status.HTTP_201_CREATED,
        )
        answer_id = create_response.data["id"]

        update_response = self.client.patch(
            reverse("answertext-detail", args=[answer_id]),
            {"body": "updated body"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AnswerText.objects.get(pk=answer_id).body,
            "updated body",
        )

        delete_response = self.client.delete(
            reverse("answertext-detail", args=[answer_id]))
        self.assertEqual(
            delete_response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertFalse(AnswerText.objects.filter(pk=answer_id).exists())

    def test_submitted_sheet_rejects_answer_create(self):
        self.client.force_login(self.respondent)

        response = self.client.post(
            reverse("answertext-list"),
            {
                "question": self.optional_question.pk,
                "answersheet": self.submitted.pk,
                "body": "late answer",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(AnswerText.objects.filter(
            question=self.optional_question,
            answersheet=self.submitted,
        ).exists())

    def test_submitted_sheet_rejects_answer_update(self):
        self.client.force_login(self.respondent)

        response = self.client.patch(
            reverse(
                "answertext-detail",
                args=[self.submitted_answer.pk],
            ),
            {"body": "changed after submit"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.submitted_answer.refresh_from_db()
        self.assertEqual(self.submitted_answer.body, "submitted body")

    def test_submitted_sheet_rejects_answer_delete(self):
        self.client.force_login(self.respondent)

        response = self.client.delete(reverse(
            "answertext-detail",
            args=[self.submitted_answer.pk],
        ))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(AnswerText.objects.filter(
            pk=self.submitted_answer.pk).exists())

    def test_nonowners_cannot_create_draft_answer(self):
        for actor in (self.asker, self.unrelated, self.staff):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)

                response = self.client.post(
                    reverse("answertext-list"),
                    {
                        "question": self.optional_question.pk,
                        "answersheet": self.draft.pk,
                        "body": f"created by {actor.username}",
                    },
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertFalse(AnswerText.objects.filter(
                    question=self.optional_question,
                    answersheet=self.draft,
                ).exists())

    def test_nonowners_cannot_update_draft_answer(self):
        for actor in (self.asker, self.unrelated, self.staff):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)

                response = self.client.patch(
                    reverse(
                        "answertext-detail",
                        args=[self.draft_answer.pk],
                    ),
                    {"body": f"changed by {actor.username}"},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.draft_answer.refresh_from_db()
                self.assertEqual(self.draft_answer.body, "draft body")

    def test_nonowners_cannot_delete_draft_answer(self):
        for index, actor in enumerate(
            (self.asker, self.unrelated, self.staff),
            start=10,
        ):
            with self.subTest(actor=actor.username):
                question = Question.objects.create(
                    survey=self.survey,
                    order=index,
                    topic=f"Delete target {index}",
                    type=Question.Type.TEXT,
                )
                answer = AnswerText.objects.create(
                    question=question,
                    answersheet=self.draft,
                    body="must remain",
                )
                self.client.force_login(actor)

                response = self.client.delete(reverse(
                    "answertext-detail",
                    args=[answer.pk],
                ))

                self.assertEqual(
                    response.status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.assertTrue(
                    AnswerText.objects.filter(pk=answer.pk).exists())

    def test_survey_owner_sees_only_submitted_answers(self):
        self.client.force_login(self.asker)

        response = self.client.get(reverse("answertext-survey-owner"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["id"] for row in response.data],
            [self.submitted_answer.pk],
        )

    def test_survey_owner_can_retrieve_only_submitted_answer(self):
        self.client.force_login(self.asker)

        submitted_response = self.client.get(reverse(
            "answertext-detail",
            args=[self.submitted_answer.pk],
        ))
        draft_response = self.client.get(reverse(
            "answertext-detail",
            args=[self.draft_answer.pk],
        ))

        self.assertEqual(
            submitted_response.status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            draft_response.status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_staff_has_no_answer_text_read_bypass(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse(
            "answertext-detail",
            args=[self.submitted_answer.pk],
        ))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_answer_owner_reads_own_draft_and_submitted_answers(self):
        self.client.force_login(self.respondent)

        response = self.client.get(reverse("answertext-answer-owner"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {row["id"] for row in response.data},
            {self.draft_answer.pk, self.submitted_answer.pk},
        )


class AnswerSheetConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.asker = User.objects.create_user(
            username="v10_race_asker",
            name="V10 Race Asker",
        )
        self.respondent = User.objects.create_user(
            username="v10_race_respondent",
            name="V10 Race Respondent",
        )
        now = datetime.now()
        self.survey = Survey.objects.create(
            title="V10 race survey",
            creator=self.asker,
            status=Survey.Status.PUBLISHED,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )
        self.required_question = Question.objects.create(
            survey=self.survey,
            order=1,
            topic="Required answer",
            type=Question.Type.TEXT,
            required=True,
        )
        self.optional_question = Question.objects.create(
            survey=self.survey,
            order=2,
            topic="Optional answer",
            type=Question.Type.TEXT,
            required=False,
        )
        self.sheet = AnswerSheet.objects.create(
            survey=self.survey,
            creator=self.respondent,
        )
        self.required_answer = AnswerText.objects.create(
            question=self.required_question,
            answersheet=self.sheet,
            body="committed before submit",
        )

    @staticmethod
    def _is_sheet_lock(sql):
        normalized = " ".join(sql.upper().split())
        return (
            "FOR UPDATE" in normalized
            and "QUESTIONNAIRE_ANSWERSHEET" in normalized
        )

    def _race_submit_against_mutation(self, method, path, data=None):
        submit_has_lock = Event()
        allow_submit = Event()
        mutation_attempted_lock = Event()
        mutation_done = Event()
        results = {}
        errors = []
        submit_paused = {"value": False}
        mutation_signaled = {"value": False}

        def pause_submit_after_lock(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (
                not submit_paused["value"]
                and self._is_sheet_lock(sql)
            ):
                submit_paused["value"] = True
                submit_has_lock.set()
                if not allow_submit.wait(10):
                    raise TimeoutError("submit lock was not released by test")
            return result

        def signal_mutation_lock_attempt(execute, sql, params, many, context):
            if (
                not mutation_signaled["value"]
                and self._is_sheet_lock(sql)
            ):
                mutation_signaled["value"] = True
                mutation_attempted_lock.set()
            return execute(sql, params, many, context)

        def submit_worker():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.respondent)
            try:
                with connection.execute_wrapper(pause_submit_after_lock):
                    response = client.post(
                        reverse("answersheet-submit", args=[self.sheet.pk]),
                        {},
                        format="json",
                    )
                results["submit"] = response.status_code
            except BaseException as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def mutation_worker():
            close_old_connections()
            client = APIClient()
            client.force_authenticate(user=self.respondent)
            try:
                with connection.execute_wrapper(signal_mutation_lock_attempt):
                    request_method = getattr(client, method)
                    response = request_method(path, data or {}, format="json")
                results["mutation"] = response.status_code
            except BaseException as exc:
                errors.append(exc)
            finally:
                mutation_done.set()
                close_old_connections()

        submit_thread = Thread(target=submit_worker)
        mutation_thread = Thread(target=mutation_worker)
        submit_thread.start()
        mutation_started = False
        try:
            self.assertTrue(
                submit_has_lock.wait(10),
                "submit did not acquire the answer-sheet row lock",
            )
            mutation_thread.start()
            mutation_started = True
            self.assertTrue(
                mutation_attempted_lock.wait(10),
                "answer mutation did not attempt the same row lock",
            )
            self.assertFalse(
                mutation_done.wait(0.25),
                "answer mutation completed while submit held the row lock",
            )
        finally:
            allow_submit.set()
            submit_thread.join(10)
            if mutation_started:
                mutation_thread.join(10)

        self.assertFalse(submit_thread.is_alive(), "submit thread did not finish")
        self.assertFalse(
            mutation_thread.is_alive(),
            "answer mutation thread did not finish",
        )
        if errors:
            raise errors[0]
        self.assertEqual(results["submit"], status.HTTP_200_OK)
        self.assertEqual(results["mutation"], status.HTTP_400_BAD_REQUEST)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, AnswerSheet.Status.SUBMITTED)

    def test_submit_wins_race_against_answer_create(self):
        self._race_submit_against_mutation(
            "post",
            reverse("answertext-list"),
            {
                "question": self.optional_question.pk,
                "answersheet": self.sheet.pk,
                "body": "must be rejected",
            },
        )

        self.assertFalse(AnswerText.objects.filter(
            answersheet=self.sheet,
            question=self.optional_question,
        ).exists())

    def test_submit_wins_race_against_answer_update(self):
        self._race_submit_against_mutation(
            "patch",
            reverse("answertext-detail", args=[self.required_answer.pk]),
            {"body": "must be rejected"},
        )

        self.required_answer.refresh_from_db()
        self.assertEqual(
            self.required_answer.body,
            "committed before submit",
        )

    def test_submit_wins_race_against_answer_delete(self):
        self._race_submit_against_mutation(
            "delete",
            reverse("answertext-detail", args=[self.required_answer.pk]),
        )

        self.assertTrue(
            AnswerText.objects.filter(pk=self.required_answer.pk).exists())
