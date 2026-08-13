from rest_framework import permissions

from questionnaire.models import AnswerSheet

__all__ = [
    'IsTextOwnerOrAsker',
    'IsSheetOwnerOrAsker',
    'IsSurveyOwnerOrReadOnly',
    'IsQuestionOwnerOrReadOnly',
    'IsChoiceOwnerOrReadOnly',
]


def check_owner_or_asker(request, owner, asker):
    return request.user.is_staff or request.user == owner or request.user == asker


class IsTextOwnerOrAsker(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        owner = obj.answersheet.creator
        asker = obj.question.survey.creator
        return check_owner_or_asker(request, owner, asker)


class IsSheetOwnerOrAsker(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user == obj.creator:
            return True
        return (
            request.method in permissions.SAFE_METHODS
            and obj.status == AnswerSheet.Status.SUBMITTED
            and request.user == obj.survey.creator
        )


def check_owner_or_read_only(request, owner):
    return (request.user.is_staff 
            or request.method in permissions.SAFE_METHODS
            or request.user == owner)


class IsSurveyOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        owner = obj.creator
        return check_owner_or_read_only(request, owner)


class IsQuestionOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        owner = obj.survey.creator
        return check_owner_or_read_only(request, owner)


class IsChoiceOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        owner = obj.question.survey.creator
        return check_owner_or_read_only(request, owner)
