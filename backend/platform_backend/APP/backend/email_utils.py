import random
import string
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType
from APP.backend.config import email_conf

# 配置 FastMail
conf = ConnectionConfig(
    MAIL_USERNAME=email_conf.MAIL_USERNAME,
    MAIL_PASSWORD=email_conf.MAIL_PASSWORD,
    MAIL_FROM=email_conf.MAIL_FROM,
    MAIL_PORT=email_conf.MAIL_PORT,
    MAIL_SERVER=email_conf.MAIL_SERVER,
    MAIL_STARTTLS=email_conf.MAIL_STARTTLS,
    MAIL_SSL_TLS=email_conf.MAIL_SSL_TLS,
    USE_CREDENTIALS=email_conf.USE_CREDENTIALS,
    VALIDATE_CERTS=email_conf.VALIDATE_CERTS
)

def generate_verification_code(length=6):
    """生成6位数字验证码"""
    return ''.join(random.choices(string.digits, k=length))

async def send_verification_email(email: str, code: str, purpose: str):
    """发送验证码邮件"""
    subject_map = {
        "register": "【Qwen Chat】注册验证码",
        "reset": "【Qwen Chat】重置密码验证码"
    }
    
    subject = subject_map.get(purpose, "Qwen Chat 验证码")
    
    html = f"""
    <div style="padding: 20px; border: 1px solid #ccc;">
        <h2>您的验证码是：</h2>
        <h1 style="color: #4f46e5; letter-spacing: 5px;">{code}</h1>
        <p>有效时间 5 分钟，请勿泄露给他人。</p>
    </div>
    """

    message = MessageSchema(
        subject=subject,
        recipients=[email],
        body=html,
        subtype=MessageType.html
    )

    fm = FastMail(conf)
    await fm.send_message(message)