import asyncio
import html
import json
import re
import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi_mail import FastMail, MessageSchema, MessageType

from APP.backend.database import SessionLocal, UserModel
from APP.backend.email_utils import conf

MAX_DELAY_MINUTES = 60 * 24 * 30
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match((value or "").strip()))


def _resolve_user_email(user_id: int | None) -> str:
    if not user_id:
        return ""
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.id == user_id).first()
        return (user.email or "").strip() if user else ""
    finally:
        db.close()


def _send_reminder_email_sync(email: str, subject: str, content: str) -> None:
    safe_content = html.escape(content or "健康提醒")
    body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 24px; border: 1px solid #d1fae5; border-radius: 16px; background: #f0fdf4; color: #064e3b;">
        <h2 style="margin: 0 0 12px; color: #047857;">司宁健康管理助手提醒</h2>
        <p style="font-size: 16px; line-height: 1.7; margin: 0;">{safe_content}</p>
        <p style="font-size: 12px; color: #64748b; margin-top: 20px;">这是一封由你设置的定时提醒邮件。</p>
    </div>
    """
    message = MessageSchema(
        subject=subject or "司宁健康管理提醒",
        recipients=[email],
        body=body,
        subtype=MessageType.html,
    )
    asyncio.run(FastMail(conf).send_message(message))


def schedule_email_reminder(email: str, delay_minutes: int, reminder: str, subject: str = "司宁健康管理提醒", user_id: int | None = None) -> str:
    """Schedule a one-shot reminder email in a daemon timer thread."""
    email = (email or "").strip()
    reminder = (reminder or "").strip()
    if not _is_valid_email(email):
        email = _resolve_user_email(user_id)
    if not _is_valid_email(email):
        return "定时邮件提醒创建失败：请提供有效邮箱地址，或先让用户登录并绑定邮箱。"
    try:
        minutes = int(delay_minutes)
    except Exception:
        return "定时邮件提醒创建失败：delay_minutes 必须是整数分钟。"
    if minutes <= 0:
        return "定时邮件提醒创建失败：提醒时间必须晚于当前时间。"
    if minutes > MAX_DELAY_MINUTES:
        return f"定时邮件提醒创建失败：当前最多支持 {MAX_DELAY_MINUTES} 分钟内的提醒。"
    if not reminder:
        return "定时邮件提醒创建失败：请提供提醒内容。"

    now_beijing = datetime.now(BEIJING_TZ)
    send_at_beijing = now_beijing + timedelta(minutes=minutes)
    send_at_utc = send_at_beijing.astimezone(ZoneInfo("UTC"))

    def _job() -> None:
        try:
            _send_reminder_email_sync(email, subject, reminder)
        except Exception as exc:
            print(f"[ReminderEmail] failed user_id={user_id}: {exc}")

    timer = threading.Timer(minutes * 60, _job)
    timer.daemon = True
    timer.start()
    return (
        f"已创建定时邮件提醒：将在约 {minutes} 分钟后发送到 {email}。"
        f"提醒内容：{reminder}。"
        f"计划发送时间：{send_at_beijing.isoformat(timespec='seconds')}（北京时间，UTC {send_at_utc.isoformat(timespec='seconds')}）。"
    )


def schedule_email_reminder_from_args(args: dict[str, Any], user_id: int | None = None) -> str:
    if not isinstance(args, dict):
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                args = parsed if isinstance(parsed, dict) else {}
            except Exception:
                args = {}
        else:
            args = {}
    return schedule_email_reminder(
        email=str(args.get("email") or ""),
        delay_minutes=args.get("delay_minutes") or args.get("minutes") or 0,
        reminder=str(args.get("reminder") or args.get("content") or ""),
        subject=str(args.get("subject") or "司宁健康管理提醒"),
        user_id=user_id,
    )
