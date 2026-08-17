"""Issue and redeem digest-only credentials for pending WeChat bindings."""

import hashlib
import secrets
from datetime import datetime, timedelta

from django.contrib.auth import authenticate
from django.core import signing
from django.db import IntegrityError, transaction

from api.config import CONFIG
from generic.models import PendingWechatBinding, User, UserWechatProfile

__all__ = [
    "BINDING_SIGNING_SALT",
    "WechatBindingError",
    "WechatBindingAuthenticationError",
    "issue_binding_credential",
    "redeem_binding_credential",
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


def _unsign_nonce(signed_openid: str) -> str:
    signer = signing.TimestampSigner(salt=BINDING_SIGNING_SALT)
    try:
        return signer.unsign(
            signed_openid,
            max_age=CONFIG.signed_openid_ttl_minutes * 60,
        )
    except signing.SignatureExpired as exc:
        raise WechatBindingError(
            "绑定凭据已过期，请重新登录微信授权"
        ) from exc
    except signing.BadSignature as exc:
        raise WechatBindingError(
            "无效的绑定凭据，请重新登录微信授权"
        ) from exc


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


def redeem_binding_credential(
    signed_openid: str,
    username: str,
    password: str,
) -> User:
    """Atomically authenticate, bind, and consume a one-time credential."""
    nonce_digest = _nonce_digest(_unsign_nonce(signed_openid))
    now = datetime.now()
    error: WechatBindingError | None = None
    bound_user: User | None = None

    with transaction.atomic():
        try:
            pending = PendingWechatBinding.objects.select_for_update().get(
                nonce_digest=nonce_digest
            )
        except PendingWechatBinding.DoesNotExist:
            error = WechatBindingError(
                "绑定凭据无效或已使用，请重新登录微信授权"
            )
        else:
            if pending.expires_at <= now:
                pending.delete()
                error = WechatBindingError(
                    "绑定凭据已过期，请重新登录微信授权"
                )
            else:
                candidate = authenticate(
                    username=username,
                    password=password,
                )
                if candidate is None:
                    pending.failed_attempts += 1
                    if pending.failed_attempts >= max(
                        1, CONFIG.binding_max_failed_attempts
                    ):
                        pending.delete()
                    else:
                        pending.save(update_fields=["failed_attempts"])
                    error = WechatBindingAuthenticationError(
                        "账号或密码错误"
                    )
                elif candidate.is_org():
                    error = WechatBindingError(
                        "请使用小组管理员的个人账户绑定",
                        field="username",
                    )
                elif not candidate.is_person():
                    error = WechatBindingError(
                        "该类型账户暂时不支持微信小程序",
                        field="username",
                    )
                elif UserWechatProfile.objects.filter(
                    user=candidate
                ).exists():
                    error = WechatBindingError(
                        "该账户已绑定微信",
                        field="username",
                    )
                elif UserWechatProfile.objects.filter(
                    openid=pending.openid
                ).exists():
                    pending.delete()
                    error = WechatBindingError("该微信已绑定其他账号")
                else:
                    try:
                        with transaction.atomic():
                            UserWechatProfile.objects.create(
                                user=candidate,
                                openid=pending.openid,
                            )
                    except IntegrityError:
                        pending.delete()
                        error = WechatBindingError(
                            "微信或账户已被并发绑定"
                        )
                    else:
                        pending.delete()
                        bound_user = candidate

    if error is not None:
        raise error
    if bound_user is None:
        raise RuntimeError(
            "binding completed without a user or controlled error"
        )
    return bound_user
