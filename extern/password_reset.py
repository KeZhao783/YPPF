"""Non-persistent, bounded delivery for password-reset credentials."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import BoundedSemaphore
from typing import Callable

import requests

from app.config import CONFIG
from extern.log import ExternLogger
from extern.wechat import send_password_reset_token


_DELIVERY_WORKERS = 4
_DELIVERY_CAPACITY = 16
_delivery_executor = ThreadPoolExecutor(
    max_workers=_DELIVERY_WORKERS,
    thread_name_prefix="password-reset-delivery",
)
_delivery_slots = BoundedSemaphore(_DELIVERY_CAPACITY)
logger = ExternLogger.getLogger("password_reset_delivery")


def _run_delivery(delivery: Callable, args: tuple) -> None:
    try:
        delivery(*args)
    except Exception:
        # Delivery errors must not copy the credential into logs.
        logger.error("Password-reset credential delivery failed")
    finally:
        _delivery_slots.release()


def _queue_delivery(delivery: Callable, *args) -> bool:
    if not _delivery_slots.acquire(blocking=False):
        logger.warning("Password-reset delivery queue is full")
        return False
    try:
        _delivery_executor.submit(_run_delivery, delivery, args)
    except RuntimeError:
        _delivery_slots.release()
        logger.warning("Password-reset delivery queue is unavailable")
        return False
    return True


def _deliver_password_reset_email(
    person_name: str,
    email: str,
    token: str,
) -> None:
    message = (
        f"<h3><b>亲爱的{person_name}同学：</b></h3><br/>"
        "您好！本次密码重置凭证为：<br/>"
        f'<p style="color:orange">{token}</p>'
        "凭证十分钟内有效，且只能使用一次。<br/>"
        "<br/>元培学院开发组<br/>"
        + datetime.now().strftime("%Y年%m月%d日")
    )
    post_data = json.dumps({
        "sender": "元培学院开发组",
        "toaddrs": [email],
        "subject": "YPPF密码重置",
        "content": message,
        "html": True,
        "private_level": 0,
        "secret": CONFIG.email.hasher.encode(message),
    })
    requests.post(
        CONFIG.email.url,
        post_data,
        timeout=6,
    )


def queue_password_reset_email(
    person_name: str,
    email: str,
    token: str,
) -> bool:
    return _queue_delivery(
        _deliver_password_reset_email,
        person_name,
        email,
        token,
    )


def queue_password_reset_wechat(username: str, token: str) -> bool:
    return _queue_delivery(send_password_reset_token, username, token)
