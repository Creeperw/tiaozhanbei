from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from APP.backend.database import ExternalIdentityLink, get_db, UserModel
from APP.backend.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_DEFAULT_PASSWORD


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token", auto_error=False)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def _host_user(request: Request):
    return getattr(request.state, "current_user", None)


def _get_or_create_host_user(db: Session, host_user) -> UserModel:
    external_user_id = str(host_user.user_id)
    link = db.query(ExternalIdentityLink).filter(
        ExternalIdentityLink.provider == "competition_app",
        ExternalIdentityLink.external_user_id == external_user_id,
    ).first()
    if link is not None:
        user = db.query(UserModel).filter(UserModel.id == link.user_id).one()
        host_role = str(getattr(host_user, "role", "user") or "user")
        if user.role != host_role:
            user.role = host_role
            db.commit()
            db.refresh(user)
        return user

    identity_hash = hashlib.sha256(external_user_id.encode("utf-8")).hexdigest()[:32]
    user = UserModel(
        username=f"core_{identity_hash}",
        email=f"core_{identity_hash}@local.invalid",
        hashed_password=pwd_context.hash(secrets.token_urlsafe(32)),
        role=str(getattr(host_user, "role", "user") or "user"),
    )
    try:
        db.add(user)
        db.flush()
        db.add(
            ExternalIdentityLink(
                provider="competition_app",
                external_user_id=external_user_id,
                user_id=user.id,
            )
        )
        db.commit()
    except IntegrityError:
        # Two first requests for the same host user may race. The unique link
        # is authoritative; the losing transaction reuses the committed row.
        db.rollback()
        link = db.query(ExternalIdentityLink).filter(
            ExternalIdentityLink.provider == "competition_app",
            ExternalIdentityLink.external_user_id == external_user_id,
        ).first()
        if link is None:
            raise
        return db.query(UserModel).filter(UserModel.id == link.user_id).one()
    db.refresh(user)
    return user


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception
        except JWTError:
            raise credentials_exception

        user = db.query(UserModel).filter(UserModel.username == username).first()
        if user is None:
            raise credentials_exception
        return user

    host_user = _host_user(request)
    if host_user is not None:
        return _get_or_create_host_user(db, host_user)
    raise credentials_exception

def require_admin_user(current_user: UserModel = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return current_user

def ensure_default_admin(db: Session):
    admin = db.query(UserModel).filter(UserModel.username == ADMIN_USERNAME).first()
    if admin:
        if admin.role != "admin":
            admin.role = "admin"
            db.commit()
        return admin
    email_owner = db.query(UserModel).filter(UserModel.email == ADMIN_EMAIL).first()
    if email_owner:
        if email_owner.role != "admin":
            email_owner.role = "admin"
            db.commit()
        return email_owner
    admin = UserModel(
        username=ADMIN_USERNAME,
        email=ADMIN_EMAIL,
        hashed_password=get_password_hash(ADMIN_DEFAULT_PASSWORD),
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
