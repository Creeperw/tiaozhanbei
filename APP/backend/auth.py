from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from APP.backend.database import get_db, UserModel
from APP.backend.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_DEFAULT_PASSWORD


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
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