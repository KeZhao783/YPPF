from datetime import datetime

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied

from questionnaire.models import AnswerSheet, AnswerText, Survey
from questionnaire.validators import validate_answer_body

__all__ = [
    'lock_draft_answersheet',
    'submit_answersheet',
]


def lock_draft_answersheet(sheet_id, actor):
    """Lock a sheet row and require it still be a draft owned by actor."""
    sheet = AnswerSheet.objects.select_for_update().get(pk=sheet_id)
    if sheet.creator_id != actor.pk:
        raise PermissionDenied("只有答卷创建者才能修改答卷！")
    if sheet.status != AnswerSheet.Status.DRAFT:
        raise serializers.ValidationError("已提交答卷不能修改！")
    return sheet


def submit_answersheet(sheet_id, actor, now=None):
    now = datetime.now() if now is None else now
    with transaction.atomic():
        try:
            sheet = (
                AnswerSheet.objects
                .select_for_update()
                .select_related('survey')
                .get(pk=sheet_id)
            )
        except AnswerSheet.DoesNotExist as exc:
            raise NotFound("答卷不存在或已被删除！") from exc
        if sheet.creator_id != actor.pk:
            raise PermissionDenied("只有答卷创建者才能提交答卷！")
        if sheet.status != AnswerSheet.Status.DRAFT:
            raise serializers.ValidationError("答卷已经提交！")

        survey = sheet.survey
        if survey.status != Survey.Status.PUBLISHED:
            raise serializers.ValidationError("只能提交已发布的问卷！")
        if not survey.start_time <= now <= survey.end_time:
            raise serializers.ValidationError("当前不在问卷提交时间内！")

        questions = list(
            survey.questions.prefetch_related('choices').order_by('order'))
        question_by_id = {
            question.pk: question
            for question in questions
        }
        valid_choice_orders = {
            question.pk: {
                choice.order
                for choice in question.choices.all()
            }
            for question in questions
            if question.have_choice()
        }
        answer_by_question = {}
        for answer in AnswerText.objects.filter(answersheet=sheet):
            question = question_by_id.get(answer.question_id)
            if question is None:
                raise serializers.ValidationError(
                    "答案与答卷不属于同一问卷！")
            if question.pk in answer_by_question:
                raise serializers.ValidationError(
                    "同一问题存在重复答案！")

            body = (answer.body or '').strip()
            if not body:
                raise serializers.ValidationError("答案不能为空！")
            try:
                validate_answer_body(
                    question,
                    body,
                    valid_choice_orders.get(question.pk),
                )
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages) from exc
            answer_by_question[question.pk] = answer

        if any(
            question.required and question.pk not in answer_by_question
            for question in questions
        ):
            raise serializers.ValidationError("必填题尚未完成！")

        sheet.status = AnswerSheet.Status.SUBMITTED
        sheet.save(update_fields=['status'])
        return sheet
