# V10 Secure Survey Submission Design

## Purpose

Prevent a survey creator from submitting or editing another user's draft
answer sheet. Submission becomes a named server-side transition performed only
by the answer-sheet creator, while survey creators retain read-only access to
submitted results from surveys they created.

## Scope

This is the minimum security repair approved for V10:

- disable generic `PUT` and `PATCH` on answer sheets;
- make `AnswerSheet.status` read-only in the API contract;
- add `POST /questionnaire/answersheet/{id}/submit/`;
- allow only the answer-sheet creator to submit their draft;
- validate the survey state, submission window, required answers, answer
  ownership, duplicates, and answer bodies before submission;
- perform validation and `DRAFT -> SUBMITTED` in one short transaction while
  holding a row lock on the answer sheet;
- expose only `SUBMITTED` sheets and answers to the creator of a survey;
- allow `AnswerText` create, update, and delete only when the caller owns the
  answer sheet and it is still `DRAFT`;
- serialize answer mutations with submission by locking the same answer-sheet
  row before each mutation;
- route the existing dormitory routine-questionnaire workflow through the
  same canonical submission transition.

There are no model changes or migrations. The change does not add audit
records, withdrawal, a new state, compatibility support for generic status
PATCH, or unrelated questionnaire refactoring.

## API Contract

### Answer sheets

`AnswerSheetSerializer` exposes the explicit fields `id`, `survey`, `creator`,
`create_time`, and `status`. `creator` remains a server-populated hidden field;
`id`, `create_time`, and `status` are read-only. A create request that supplies
`status` cannot create a submitted sheet.

`AnswerSheetViewSet.http_method_names` excludes `put` and `patch`, so both
generic update routes return HTTP 405.

The new detail action is:

```text
POST /questionnaire/answersheet/{id}/submit/
request body: empty
success: 200 with the serialized submitted answer sheet
```

The submit action scopes object lookup to sheets owned by the current user.
An authenticated non-owner receives HTTP 404 without learning whether the ID
belongs to another user. Anonymous users and session requests without a valid
CSRF token are rejected by the existing authentication stack.

Business validation failures return HTTP 400 and leave the sheet unchanged.
This includes repeated submission, inactive survey state, calls outside the
survey time window, missing required answers, duplicate answers, cross-survey
answers, and invalid answer bodies.

### Result reads

An answer-sheet owner can read their own draft or submitted sheets. A survey
creator can retrieve only submitted sheets for surveys they created. The
`survey_owner` action applies the same `SUBMITTED` filter.

The same rule applies to answer text: an owner can read answers from their own
sheets, while a survey creator can read answers only from submitted sheets in
their own surveys. Staff status alone does not bypass these object boundaries.

### Answer mutation

`AnswerText` creation, update, and deletion require both:

1. `request.user` is the creator of the related answer sheet; and
2. the answer sheet is currently `DRAFT` after it has been locked.

Existing relation checks remain in force: the question must belong to the
answer sheet's survey, and an answer for the same question cannot be created
twice. Updates cannot move an answer to another sheet or question.

## Domain Operation

`questionnaire.utils.submit_answersheet(sheet_id, actor, now=None)` owns the
state transition. It captures `datetime.now()` once when no test time is
injected, then enters `transaction.atomic()` and locks the authoritative
`AnswerSheet` with `select_for_update()`.

After locking, it verifies:

1. the actor owns the sheet;
2. the current state is `DRAFT`;
3. the survey is `PUBLISHED`;
4. `start_time <= now <= end_time`;
5. every answer references a question from the sheet's survey;
6. no question has more than one answer in the sheet;
7. every required question has a non-empty answer;
8. every stored body passes `validate_answer_body()`.

Only after every check succeeds does it set `status=SUBMITTED` and save only
that field. Any exception rolls the transaction back.

Answer mutations use a small shared helper in the same module to lock the
sheet and re-check owner and `DRAFT` state. Submission and mutation therefore
serialize on the same database row: whichever operation gets the lock first
finishes under its valid preconditions; the second operation re-reads and
checks the resulting state.

The dormitory routine-questionnaire view creates the sheet and answers, then
calls this transition inside the same outer transaction. A validation failure
therefore rolls back the entire response instead of leaving a partial draft.

## Authorization Layers

The queryset is the first visibility boundary and is selected by action:

- submit and destructive answer-sheet operations: current user's sheets;
- answer-sheet reads: current user's sheets plus submitted results for surveys
  they created;
- answer-text mutations: answers in current user's sheets;
- answer-text reads: current user's answers plus submitted results for surveys
  they created.

Object permissions provide defense in depth. Owners retain their intended
access. Survey creators are accepted only for safe methods and only for
submitted results already admitted by the queryset. The previous unconditional
staff/asker write bypass is removed.

The domain operation repeats owner and state validation after acquiring the
lock. A pre-lock queryset or permission result is not treated as authoritative
for a mutable state transition.

## Testing

API tests use real DRF routing, session authentication, and database models.
They cover:

- generic answer-sheet `PUT` and `PATCH` return 405;
- injected create status remains `DRAFT` and creator is server-controlled;
- only the owner can submit a complete draft;
- survey creator, unrelated user, staff, anonymous user, and missing-CSRF
  session cannot submit;
- missing/duplicate/cross-survey/invalid answers, invalid survey state, time
  boundaries, and repeat submission fail without changing state;
- survey-owner reads contain only submitted sheets and answers;
- answer create/update/delete succeeds for the owner of a draft and fails for
  other roles or a submitted sheet;
- submission failures do not partially update the sheet or its answers;
- separate database connections prove that create, update, and delete block
  on a concurrent submission and reject their mutation after it commits;
- the dormitory workflow produces a submitted sheet visible to the survey
  creator;
- a multi-choice-question submission reuses its prefetched choices instead of
  querying choices once per answer while holding the sheet lock.

The questionnaire tests run first during development, followed by the full
Django suite and `makemigrations --check --dry-run` before the PR is created.

## Compatibility

Any caller that used generic answer-sheet `PUT` or `PATCH` to submit must move
to the new `POST .../submit/` action. The separately maintained mini-program
repository was checked and currently contains no references to the
questionnaire answer-sheet or answer-text endpoints. Retaining status PATCH as
a compatibility path would preserve the vulnerable contract and is therefore
out of scope.

Existing `DRAFT` rows are deliberately not migrated. The database does not
record whether an old draft represents a completed legacy dormitory response
or a genuinely incomplete answer, so automatically promoting all of them
would expose unsubmitted data. They remain hidden from survey-creator result
endpoints and require a separately reviewed data audit if historical results
must be recovered.
