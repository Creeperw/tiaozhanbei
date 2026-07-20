import os
import shutil
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from APP.backend.config import UPLOAD_DIR
from APP.backend.voice_recognize import transcribe_audio

router = APIRouter()

@router.post("/voice/transcribe")
async def transcribe_voice(file: UploadFile = File(...)):
    # 确保上传目录存在
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)

    # 保存临时音频文件
    file_id = str(uuid.uuid4())
    # 假设前端录音为 webm 或 wav 格式，ffmpeg/whisper 通常都能处理
    temp_filename = f"voice_{file_id}_{file.filename}"
    temp_file_path = os.path.join(UPLOAD_DIR, temp_filename)
    
    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # 调用识别
        text = transcribe_audio(temp_file_path)
        
        return {"text": text}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"识别失败: {str(e)}")
        
    finally:
        # 清理临时文件
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass