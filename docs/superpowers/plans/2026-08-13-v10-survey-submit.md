# V10 Secure Survey Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Replace writable answer-sheet status with an owner-only atomic submit action and enforce draft-owner mutation boundaries for answer text.

**Architecture:** DRF querysets and method-aware object permissions enforce visibility. A focused questionnaire utility owns the locked state transition and the shared draft lock used by answer mutations, so authorization and mutable state are re-checked at the authoritative write point.

**Tech Stack:** Django 5.2, Django REST Framework 3.17, MySQL 8, Django TestCase/TransactionTestCase, Docker Compose.

## Global Constraints

- Base all work on upstream/develop; do not include the V11 commit.
- Do not change models or add migrations, audit records, withdrawal states, dependencies, or unrelated refactors.
- Disable answer-sheet PUT and PATCH; do not retain a compatibility status-write path.
- Use naive local datetime.now() because project USE_TZ is false.
- Exercise real DRF routing, session authentication, CSRF, models, and database behavior in tests.
- Keep changes confined to questionnaire code and V10 documentation.

---

### Task 1: Close generic answer-sheet writes and scope result reads

**Files:**
- Create: questionnaire/test_answersheet_security.py
- Modify: questionnaire/serializers.py:41-46
- Modify: questionnaire/permissions.py:12-27
- Modify: questionnaire/views.py:130-170

**Interfaces:**
- Consumes: router basename answersheet and AnswerSheet.Status.
- Produces: explicit AnswerSheetSerializer fields; owner/survey-creator read boundary; HTTP 405 for generic PUT/PATCH.

- [ ] **Step 1: Write the failing API boundary tests**

Create real API fixtures for an asker, respondent, unrelated user, staff user,
published survey, draft sheet, and submitted sheet. Add these behaviors:

    def test_generic_put_and_patch_are_disabled(self):
        self.client.force_login(self.respondent)
        url = reverse("answersheet-detail", args=[self.draft.pk])
        patch_response = self.client.patch(
            url,
            {"survey": self.survey.pk,
             "status": AnswerSheet.Status.SUBMITTED},
            format="json",
        )
        put_response = self.client.put(
            url,
            {"survey": self.survey.pk,
             "status": AnswerSheet.Status.SUBMITTED},
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
        self.assertEqual(self.draft.status, AnswerSheet.Status.DRAFT)

    def test_create_ignores_client_status_and_fixes_creator(self):
        self.client.force_login(self.unrelated)
        response = self.client.post(
            reverse("answersheet-list"),
            {"survey": self.survey.pk,
             "creator": self.respondent.pk,
             "status": AnswerSheet.Status.SUBMITTED},
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
        submitted = self.client.get(
            reverse("answersheet-detail", args=[self.submitted.pk]))
        draft = self.client.get(
            reverse("answersheet-detail", args=[self.draft.pk]))
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        self.assertEqual(draft.status_code, status.HTTP_404_NOT_FOUND)

- [ ] **Step 2: Run the class and verify RED**

Run:

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test questionnaire.test_answersheet_security.AnswerSheetApiSecurityTests

Expected failures: PATCH/PUT are accepted, create accepts SUBMITTED, and
survey-owner reads expose drafts.

- [ ] **Step 3: Implement the explicit serializer and read boundary**

Use this serializer contract:

    class AnswerSheetSerializer(serializers.ModelSerializer):
        creator = serializers.HiddenField(
            default=serializers.CurrentUserDefault())

        class Meta:
            model = AnswerSheet
            fields = ["id", "survey", "creator", "create_time", "status"]
            read_only_fields = ["id", "create_time", "status"]

Make IsSheetOwnerOrAsker method-aware: owners retain access; a survey creator
is allowed only for safe methods on SUBMITTED sheets. Remove the unconditional
staff bypass.

Set AnswerSheetViewSet.http_method_names to
["get", "post", "delete", "head", "options"]. For retrieve, return the
current user's sheets plus SUBMITTED sheets whose survey creator is the
current user. For all mutating detail actions, return only current-user
sheets. Change survey_owner() to filter both survey__creator=request.user and
status=SUBMITTED. Remove perform_update().

- [ ] **Step 4: Verify GREEN and commit**

Run the class command from Step 2 and then:

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test questionnaire

Commit:

    git add questionnaire/test_answersheet_security.py questionnaire/serializers.py questionnaire/permissions.py questionnaire/views.py
    git commit -m "fix: close generic answer sheet writes"

---

### Task 2: Add owner-only atomic submission

**Files:**
- Create: questionnaire/utils.py
- Modify: questionnaire/views.py:130-170
- Modify: questionnaire/test_answersheet_security.py

**Interfaces:**
- Consumes: validate_answer_body(question, body) and Task 1's scoped queryset.
- Produces: submit_answersheet(sheet_id, actor, now=None) returning AnswerSheet; POST /questionnaire/answersheet/{id}/submit/.

- [ ] **Step 1: Write failing submit tests**

Add tests proving:

- a complete draft owner receives 200 and the database changes to SUBMITTED;
- survey creator, unrelated user, and staff receive 404 and state is unchanged;
- anonymous and session-without-CSRF calls are rejected;
- repeated submission returns 400;
- non-PUBLISHED, not-yet-started, and expired surveys return 400;
- missing required, duplicate, cross-survey, empty, and invalid-choice answers
  return 400;
- every rejected submission leaves the sheet DRAFT and answers unchanged.

The key owner assertion is:

    response = self.client.post(
        reverse("answersheet-submit", args=[self.draft.pk]),
        {},
        format="json",
    )
    self.assertEqual(response.status_code, status.HTTP_200_OK)
    self.draft.refresh_from_db()
    self.assertEqual(self.draft.status, AnswerSheet.Status.SUBMITTED)

The CSRF assertion uses APIClient(enforce_csrf_checks=True), client.login(),
and deliberately omits the token, expecting HTTP 403.

- [ ] **Step 2: Run submit tests and verify RED**

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test questionnaire.test_answersheet_security.AnswerSheetSubmitTests

Expected: reverse/action failures because answersheet-submit does not exist.

- [ ] **Step 3: Implement submit_answersheet**

In questionnaire/utils.py:

1. capture now once with datetime.now() unless injected;
2. enter transaction.atomic();
3. load AnswerSheet with select_for_update() and select_related("survey");
4. verify actor ownership and DRAFT state after locking;
5. verify PUBLISHED and start_time <= now <= end_time;
6. load survey questions with choices and all sheet answers;
7. reject cross-survey and duplicate question IDs;
8. reject empty bodies and call validate_answer_body() for each stored answer;
9. ensure every required question has an answer;
10. set SUBMITTED and save(update_fields=["status"]).

Use PermissionDenied for a non-owner and DRF ValidationError for stable HTTP
400 business failures. Do not catch exceptions around the transaction.

- [ ] **Step 4: Add the detail action**

    @action(detail=True, methods=["post"])
    def submit(self, request, *args, **kwargs):
        sheet = self.get_object()
        submitted = submit_answersheet(sheet.pk, request.user)
        return Response(self.get_serializer(submitted).data)

The action's get_object() is owner-scoped; the domain operation repeats
ownership after acquiring the lock.

- [ ] **Step 5: Verify GREEN and commit**

Run the class from Step 2 and all questionnaire tests, then commit:

    git add questionnaire/utils.py questionnaire/views.py questionnaire/test_answersheet_security.py
    git commit -m "fix: add atomic answer sheet submission"

---

### Task 3: Serialize AnswerText mutations with submission

**Files:**
- Modify: questionnaire/utils.py
- Modify: questionnaire/permissions.py:12-20
- Modify: questionnaire/serializers.py:49-70
- Modify: questionnaire/views.py:80-127
- Modify: questionnaire/test_answersheet_security.py

**Interfaces:**
- Consumes: the AnswerSheet row used by submit_answersheet().
- Produces: lock_draft_answersheet(sheet_id, actor) returning AnswerSheet; owner-only draft create/update/delete; submitted-only survey-owner reads.

- [ ] **Step 1: Write failing AnswerText security tests**

Cover:

- owner creates, sparse-PATCHes, and deletes an answer in a draft;
- owner cannot create, update, or delete answers in a submitted sheet;
- asker, unrelated user, and staff cannot create/update/delete another user's
  answer;
- survey_owner returns only answers from submitted sheets;
- survey creator can retrieve a submitted answer but not a draft answer;
- answer_owner continues to return the caller's own draft and submitted data.

For every failed mutation, refresh and assert the row collection/body is
unchanged.

- [ ] **Step 2: Run the class and verify RED**

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test questionnaire.test_answersheet_security.AnswerTextSecurityTests

Expected failures: drafts leak to survey-owner, submitted sheets accept new
answers, asker/staff can delete, and sparse PATCH cannot validate.

- [ ] **Step 3: Add the shared locked draft check**

Add lock_draft_answersheet(sheet_id, actor), which must be called inside an
atomic block. It selects the sheet for update, rejects non-owners with
PermissionDenied, rejects non-DRAFT state with ValidationError, and returns
the locked sheet.

- [ ] **Step 4: Implement method-aware permissions and querysets**

For safe methods, allow the answer owner, or the survey creator when the
answer sheet is SUBMITTED. For unsafe methods, allow only the answer owner
while the sheet is DRAFT. Remove the staff bypass.

Scope mutation querysets to answersheet__creator=request.user. Scope read
querysets to the owner's rows plus SUBMITTED rows whose
answersheet__survey__creator is the current user. Filter survey_owner by
answersheet__survey__creator and answersheet__status=SUBMITTED.

- [ ] **Step 5: Lock and re-check all three mutations**

Wrap perform_create(), perform_update(), and perform_destroy() in
transaction.atomic(). Lock the sheet first, then re-check immutable
question/sheet relations and duplicates before saving or deleting.

For partial updates, AnswerTextSerializer.validate() must fall back to the
instance values:

    question = attrs.get("question", self.instance.question)
    answersheet = attrs.get("answersheet", self.instance.answersheet)
    body = (attrs.get("body", self.instance.body) or "").strip()

Do not add sleeps, test hooks, or unrelated abstractions.

- [ ] **Step 6: Verify GREEN and commit**

Run the class from Step 2, all questionnaire tests, and the complete Django
suite. Then commit:

    git add questionnaire/utils.py questionnaire/permissions.py questionnaire/serializers.py questionnaire/views.py questionnaire/test_answersheet_security.py
    git commit -m "fix: restrict draft answer mutations to owners"

---

### Task 4: Completion audit and PR

**Files:**
- Review: every file changed from upstream/develop
- Verify: migration state and complete Django suite

**Interfaces:**
- Consumes: Tasks 1-3 and the approved design.
- Produces: verified branch and GitHub pull request against develop.

- [ ] **Step 1: Verify no schema drift**

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py makemigrations --check --dry-run

Expected: No changes detected.

- [ ] **Step 2: Run fresh focused and full verification**

    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test questionnaire
    docker compose -p yppf-v10 -f .devcontainer/docker-compose.yml exec -T yppf python manage.py test
    git diff --check upstream/develop...HEAD

Expected: zero failures and no whitespace errors.

- [ ] **Step 3: Audit every approved requirement**

Confirm generic writes are disabled, status is read-only, submit is
owner-only/locked/validated, survey-owner reads exclude drafts, AnswerText
mutations lock and require draft ownership, and no migrations/unrelated files
were introduced.

- [ ] **Step 4: Push and create the PR**

Push fix/v10-survey-submit to origin and create a GitHub PR from
KeZhao783:fix/v10-survey-submit to Yuanpei-Intelligence/YPPF:develop titled
"fix: secure survey answer submission (V10)". The PR body must state the
security boundary, compatibility change, fresh test evidence, and absence of
migrations.
