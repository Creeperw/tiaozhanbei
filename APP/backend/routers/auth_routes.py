from APP.backend.time_utils import utc_now
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import or_

from APP.backend.database import get_db, UserModel, VerificationCode
from APP.backend.schemas import UserCreate, Token, SendCodeRequest, ResetPasswordRequest
from APP.backend.auth import get_password_hash, verify_password, create_access_token, get_current_user
from APP.backend.email_utils import generate_verification_code, send_verification_email
from APP.backend.diagnosis_agent_service import get_onboarding_status
from APP.backend.system_data_service import record_login_activity

router = APIRouter()

# --- 辅助函数：校验验证码 ---
def verify_code_db(email: str, code: str, purpose: str, db: Session):
    record = db.query(VerificationCode).filter(
        VerificationCode.email == email,
        VerificationCode.code == code,
        VerificationCode.purpose == purpose,
        VerificationCode.is_used == False,
        VerificationCode.expires_at > utc_now()
    ).first()
    
    if not record:
        return False
    
    # 验证通过后标记为已使用
    record.is_used = True
    db.commit()
    return True

@router.post("/send-code")
async def send_code(req: SendCodeRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """发送验证码"""
    # 检查邮箱是否已注册
    user = db.query(UserModel).filter(UserModel.email == req.email).first()
    
    if req.purpose == "register" and user:
        raise HTTPException(status_code=400, detail="该邮箱已被注册")
    if req.purpose == "reset" and not user:
        raise HTTPException(status_code=404, detail="该邮箱未注册")

    # 生成验证码
    code = generate_verification_code()
    expires_at = utc_now() + timedelta(minutes=5)
    
    # 存库
    vc = VerificationCode(
        email=req.email,
        code=code,
        purpose=req.purpose,
        expires_at=expires_at
    )
    db.add(vc)
    db.commit()
    
    # 发送邮件（后台任务，不阻塞接口）
    background_tasks.add_task(send_verification_email, req.email, code, req.purpose)
    
    return {"message": "验证码已发送"}

@router.post("/register", response_model=Token)
def register(user: UserCreate, db: Session = Depends(get_db)):
    # 1. 校验验证码
    if not verify_code_db(user.email, user.verification_code, "register", db):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")

    # 2. 检查用户名/邮箱重复
    if db.query(UserModel).filter(UserModel.username == user.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if db.query(UserModel).filter(UserModel.email == user.email).first():
        raise HTTPException(status_code=400, detail="邮箱已存在")
    
    # 3. 创建用户
    hashed_password = get_password_hash(user.password)
    new_user = UserModel(username=user.username, email=user.email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    record_login_activity(db, user_id=new_user.id)
    db.commit()

    access_token = create_access_token(data={"sub": new_user.username})
    onboarding_status = get_onboarding_status(db, new_user.id)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "needs_survey_popup": onboarding_status.get("needs_survey_popup", True),
        "onboarding_status": onboarding_status,
    }

@router.post("/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """支持用户名或邮箱登录"""
    identifier = form_data.username # 前端传过来的可能是用户名也可能是邮箱
    
    
    user = db.query(UserModel).filter(
        or_(UserModel.username == identifier, UserModel.email == identifier)
    ).first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    record_login_activity(db, user_id=user.id)
    db.commit()
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_db)):
    # 1. 校验验证码
    if not verify_code_db(req.email, req.verification_code, "reset", db):
        raise HTTPException(status_code=400, detail="验证码无效或已过期")
        
    # 2. 更新密码
    user = db.query(UserModel).filter(UserModel.email == req.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
        
    user.hashed_password = get_password_hash(req.new_password)
    db.commit()
    
    return {"message": "密码重置成功"}

@router.get("/users/me")
async def read_users_me(current_user: UserModel = Depends(get_current_user)):
    return {"username": current_user.username, "email": current_user.email, "id": current_user.id, "role": current_user.role}