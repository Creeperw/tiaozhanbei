import threading
import logging
from APP.backend.config import VOICE_MODEL_PATH, VOICE_MODE

logger = logging.getLogger(__name__)

# 模型设置
# model_size 可以是 "tiny", "base", "small", "medium", "large-v3"
# 推荐 "medium" 或 "large-v3" 以获得高精度，"small" 以获得速度平衡
MODEL_SIZE = "small"

model = None
_model_lock = threading.Lock()


def _is_enabled() -> bool:
    return VOICE_MODE == "enabled"


def _get_model():
    """首次语音识别时再加载 Whisper，避免后端启动即占用显存/内存。"""
    global model
    if model is not None:
        return model
    with _model_lock:
        if model is not None:
            return model
        # 延迟导入 torch / faster_whisper，禁用模式下不强制依赖本地安装。
        import torch
        from faster_whisper import WhisperModel
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info("正在加载语音识别模型 (%s)...", MODEL_SIZE)
        try:
            model = WhisperModel(model_size_or_path=VOICE_MODEL_PATH, device=device, compute_type=compute_type)
            logger.info("语音模型加载完毕")
        except Exception as e:
            logger.exception("语音模型加载失败: %s", e)
            model = None
    return model


def transcribe_audio(file_path: str) -> str:
    """
    将音频文件转录为文本。
    VOICE_MODE=disabled 时直接返回提示，不加载模型。
    """
    if not _is_enabled():
        logger.info("语音识别已禁用（VOICE_MODE != enabled），跳过转录")
        return "[语音识别已禁用：本地开发模式未加载 Whisper 模型]"

    active_model = _get_model()
    if not active_model:
        return "[Error: Voice model not loaded]"

    try:
        # beam_size=5 提升精度
        segments, info = active_model.transcribe(file_path, beam_size=5, language="zh")

        text = ""
        for segment in segments:
            text += segment.text

        return text.strip()
    except Exception as e:
        logger.warning("语音识别出错: %s", e)
        return ""
