from pydantic import BaseModel, EmailStr
from typing import List, Optional, Dict, Any

# --- Auth 相关 ---
class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    verification_code: str

class SendCodeRequest(BaseModel):
    email: EmailStr
    purpose: str

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    verification_code: str
    new_password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    needs_survey_popup: Optional[bool] = None
    onboarding_status: Optional[Dict[str, Any]] = None

# --- File 相关 ---
class FileMetadata(BaseModel):
    id: str
    name: str
    size: Optional[int] = 0

class UploadResponse(BaseModel):
    filename: str
    file_id: str
    content_preview: str
    message: str

# --- Chat 相关 ---
class Message(BaseModel):
    role: str
    content: str
    files: Optional[List[FileMetadata]] = [] 
    timestamp: Optional[str] = None
    tools_enabled: Optional[bool] = None
    web_search: Optional[bool] = False
    rag_search: Optional[bool] = False # 🔥 新增 RAG 开关

class SessionModel(BaseModel):
    id: str
    user_id: int
    title: str
    messages: List[Dict[str, Any]]
    created_at: float

class CreateSessionRequest(BaseModel):
    title: str = "新对话"

class UpdateSessionRequest(BaseModel):
    title: str