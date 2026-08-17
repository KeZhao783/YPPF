"""Issue digest-only, expiring credentials for pending WeChat bindings."""

import hashlib
import secrets
from datetime import datetime, timedelta

from django.core import signing
from django.db import transaction

from api.config import CONFIG
from generic.models import PendingWechatBinding

__all__ = [
    "BINDING_SIGNING_SALT",
    "WechatBindingError",
    "WechatBindingAuthenticationError",
    "issue_binding_credential",
]

BINDING_SIGNING_SALT = "wx_miniapp_binding_nonce"


class WechatBindingError(Exception):
    """Binding input failure with the client field that should receive it."""
    def __init__(self, message: str, field: str = "signed_openid"):
        super().__init__(message)
        self.field = field


class WechatBindingAuthenticationError(WechatBindingError):
    """Binding failure caused by an unauthenticated credential."""
    pass


def _nonce_digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def issue_binding_credential(openid: str) -> str:
    """Issue a signed random nonce while storing only its digest and expiry."""
    now = datetime.now()
    nonce = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=CONFIG.signed_openid_ttl_minutes)
    with transaction.atomic():
        PendingWechatBinding.objects.filter(expires_at__lte=now).delete()
        PendingWechatBinding.objects.create(
            nonce_digest=_nonce_digest(nonce),
            openid=openid,
            expires_at=expires_at,
        )
    return signing.TimestampSigner(salt=BINDING_SIGNING_SALT).sign(nonce)
