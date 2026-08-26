from datetime import datetime, timedelta

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AnswerSheetMigrationTests(TransactionTestCase):
    migrate_from = ('questionnaire', '0002_question_min_choices_max_choices')
    migrate_to = (
        'questionnaire',
        '0005_backfill_legacy_submitted_answersheets',
    )

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.User = old_apps.get_model('generic', 'User')
        self.Survey = old_apps.get_model('questionnaire', 'Survey')
        self.AnswerSheet = old_apps.get_model(
            'questionnaire',
            'AnswerSheet',
        )
        self.AnswerText = old_apps.get_model(
            'questionnaire',
            'AnswerText',
        )
        self.Question = old_apps.get_model('questionnaire', 'Question')
        self.creator = self.User.objects.create_user(
            username='v10_migration_creator',
            name='Migration Creator',
        )
        self.respondent = self.User.objects.create_user(
            username='v10_migration_respondent',
            name='Migration Respondent',
        )
        now = datetime.now()
        self.survey = self.Survey.objects.create(
            title='V10 migration survey',
            creator=self.creator,
            status=1,
            start_time=now - timedelta(days=1),
            end_time=now + timedelta(days=1),
        )

    def tearDown(self):
        self.AnswerSheet.objects.filter(
            creator_id=self.respondent.pk,
            survey_id=self.survey.pk,
        ).delete()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        super().tearDown()

    def test_duplicates_abort_before_constraint_without_deleting_data(self):
        for _ in range(2):
            self.AnswerSheet.objects.create(
                creator=self.respondent,
                survey=self.survey,
            )
        executor = MigrationExecutor(connection)

        with self.assertRaisesMessage(
            RuntimeError,
            'Duplicate questionnaire answer sheets exist',
        ):
            executor.migrate([self.migrate_to])

        self.assertEqual(
            self.AnswerSheet.objects.filter(
                creator=self.respondent,
                survey=self.survey,
            ).count(),
            2,
        )

        self.AnswerSheet.objects.filter(
            creator=self.respondent,
            survey=self.survey,
        ).order_by('pk').last().delete()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.AnswerSheet.objects.create(
                    creator=self.respondent,
                    survey=self.survey,
                )

    def test_complete_legacy_sheets_are_backfilled_without_promoting_drafts(
        self,
    ):
        self.Survey.objects.filter(pk=self.survey.pk).update(
            status=2,
            end_time=datetime.now() - timedelta(days=1),
        )
        required_question = self.Question.objects.create(
            survey=self.survey,
            order=1,
            topic='Required question',
            type='TEXT',
            required=True,
        )
        optional_question = self.Question.objects.create(
            survey=self.survey,
            order=2,
            topic='Optional question',
            type='TEXT',
            required=False,
        )
        completed_sheet = self.AnswerSheet.objects.create(
            creator=self.respondent,
            survey=self.survey,
        )
        self.AnswerText.objects.create(
            answersheet=completed_sheet,
            question=required_question,
            body='legacy completed response',
        )

        partial_user = self.User.objects.create_user(
            username='v10_migration_partial',
            name='Migration Partial',
        )
        partial_sheet = self.AnswerSheet.objects.create(
            creator=partial_user,
            survey=self.survey,
        )
        self.AnswerText.objects.create(
            answersheet=partial_sheet,
            question=optional_question,
            body='optional response only',
        )

        empty_user = self.User.objects.create_user(
            username='v10_migration_empty',
            name='Migration Empty',
        )
        empty_sheet = self.AnswerSheet.objects.create(
            creator=empty_user,
            survey=self.survey,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        migrated_apps = executor.loader.project_state(
            [self.migrate_to],
        ).apps
        MigratedAnswerSheet = migrated_apps.get_model(
            'questionnaire',
            'AnswerSheet',
        )

        self.assertEqual(
            MigratedAnswerSheet.objects.get(pk=completed_sheet.pk).status,
            1,
        )
        self.assertEqual(
            MigratedAnswerSheet.objects.get(pk=partial_sheet.pk).status,
            0,
        )
        self.assertEqual(
            MigratedAnswerSheet.objects.get(pk=empty_sheet.pk).status,
            0,
        )
