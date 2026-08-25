import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from api.auth.ticket import (
    WEBVIEW_TICKET_TTL,
    consume_webview_ticket,
    create_webview_ticket,
)
from generic.models import PendingWebviewTicket, User


def concurrent_consume(barrier, ticket):
    close_old_connections()
    try:
        barrier.wait()
        return consume_webview_ticket(ticket)
    finally:
        close_old_connections()


class WebviewTicketTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            "ticket-user",
            "Ticket User",
            User.Type.PERSON,
            password="test-password",
            is_newuser=False,
        )

    def test_issue_stores_only_digest_purpose_and_expiry(self):
        now = datetime(2026, 8, 25, 12, 0)
        with patch("api.auth.ticket.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            first = create_webview_ticket(self.user.pk)
            second = create_webview_ticket(self.user.pk)

        self.assertNotEqual(first, second)
        pending = PendingWebviewTicket.objects.get(
            token_digest=hashlib.sha256(first.encode()).hexdigest()
        )
        self.assertNotEqual(pending.token_digest, first)
        self.assertEqual(pending.user, self.user)
        self.assertEqual(
            pending.purpose,
            PendingWebviewTicket.Purpose.WEBVIEW_LOGIN,
        )
        self.assertEqual(
            pending.expires_at,
            now + timedelta(seconds=WEBVIEW_TICKET_TTL),
        )

    def test_ticket_is_consumed_once(self):
        ticket = create_webview_ticket(self.user.pk)

        self.assertEqual(consume_webview_ticket(ticket), self.user.pk)
        self.assertIsNone(consume_webview_ticket(ticket))
        self.assertFalse(PendingWebviewTicket.objects.exists())

    def test_expired_ticket_is_rejected_and_deleted(self):
        ticket = create_webview_ticket(self.user.pk)
        PendingWebviewTicket.objects.update(
            expires_at=datetime.now() - timedelta(seconds=1)
        )

        self.assertIsNone(consume_webview_ticket(ticket))
        self.assertFalse(PendingWebviewTicket.objects.exists())

    def test_wrong_purpose_ticket_is_rejected_and_deleted(self):
        ticket = create_webview_ticket(self.user.pk)
        PendingWebviewTicket.objects.update(purpose="another_purpose")

        self.assertIsNone(consume_webview_ticket(ticket))
        self.assertFalse(PendingWebviewTicket.objects.exists())


class WebviewTicketConcurrencyTestCase(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            "ticket-race-user",
            "Ticket Race User",
            User.Type.PERSON,
            password="test-password",
            is_newuser=False,
        )

    def test_concurrent_consumers_have_one_winner(self):
        ticket = create_webview_ticket(self.user.pk)
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(concurrent_consume, barrier, ticket)
                for _ in range(2)
            ]
        outcomes = [future.result() for future in futures]

        self.assertEqual(outcomes.count(self.user.pk), 1)
        self.assertEqual(outcomes.count(None), 1)
        self.assertFalse(PendingWebviewTicket.objects.exists())
