import os
import time
import uuid
import shutil
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from APP.backend.schemas import UploadResponse
from APP.backend.config import UPLOAD_DIR
from APP.backend.store import FILES, save_file_metadata
from APP.backend.auth import get_current_user
from APP.backend.database import UserModel

router = APIRouter()

@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...), current_user: UserModel = Depends(get_current_user)):
    file_id = str(uuid.uuid4())
    timestamp = int(time.time())
    _, ext = os.path.splitext(file.filename)
    safe_filename = f"{timestamp}_{file_id[:8]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {str(e)}")
    
    FILES[file_id] = {
        "original_name": file.filename,
        "saved_path": file_path,
        "file_size": os.path.getsize(file_path),
        "upload_time": timestamp,
        "uploader_id": current_user.id 
    }
    save_file_metadata()
    
    return UploadResponse(
        filename=file.filename,
        file_id=file_id,
        content_preview="文件已上传",
        message="OK"
    )