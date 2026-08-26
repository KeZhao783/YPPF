from datetime import datetime, timedelta

from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AnswerSheetUniquenessMigrationTests(TransactionTestCase):
    migrate_from = ('questionnaire', '0002_question_min_choices_max_choices')
    migrate_to = ('questionnaire', '0004_answersheet_unique_creator_survey')

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
