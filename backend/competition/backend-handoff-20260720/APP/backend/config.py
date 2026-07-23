import os
from pathlib import Path
from pydantic import BaseModel


_BACKEND_DIR = Path(__file__).resolve().parent
_RUNTIME_DIR = Path(os.getenv("BACKEND_RUNTIME_ROOT", str(_BACKEND_DIR / "runtime"))).resolve()

def _get_float_env(name: str, default: float) -> float:
    """读取浮点型环境变量，非法值自动回退默认值。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default

# --- 端口配置 ---
# FastAPI 后端端口。vLLM 规划/执行模型占用 8000，因此后端改为 7860。
API_PORT = 7860


# --- 模型配置 ---
# 语音识别 Whisper 模型本地路径。
VOICE_MODEL_PATH = "/root/autodl-tmp/APP/models/whisper_model"
# 语音识别开关：disabled=禁用（本地开发默认，不加载 Whisper）；enabled=加载本地 Whisper。
VOICE_MODE = os.getenv("VOICE_MODE", "disabled")

# --- 数据库配置 ---
# 是否启用本地 SQLite（默认 True）。本地开发免装 MySQL；生产部署时 export USE_SQLITE=false 并配置下方 MYSQL_* 环境变量。
USE_SQLITE = os.getenv("USE_SQLITE", "true").lower() != "false"
SQLITE_PATH = os.getenv("SQLITE_PATH", str(_RUNTIME_DIR / "health_agent.db"))

# MySQL 服务地址（仅在 USE_SQLITE=false 时生效）。
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
# MySQL 服务端口。
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
# MySQL 登录用户名。
MYSQL_USER = os.getenv("MYSQL_USER", "root")
# MySQL 登录密码。
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
# 业务数据库名称。
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "health_agent")
# MySQL 连接字符串；指定 utf8mb4 以支持中文和 emoji。
_MYSQL_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
# 本地默认 SQLite，生产可通过 USE_SQLITE=false 切回 MySQL。
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"sqlite:///{SQLITE_PATH}" if USE_SQLITE else _MYSQL_URL
)

# --- LLM 调用开关 ---
# 模型调用模式：api=远程 API（本地开发默认）；local=本地 vLLM OpenAI 兼容服务（部署时切回）。
# 切换方式：export LLM_MODE=local 即可回到本地 vLLM，不影响业务代码。
LLM_MODE = os.getenv("LLM_MODE", "api")

# --- API 模式配置（Anthropic 兼容端点） ---
# 远程 LLM API Key。生产环境建议通过环境变量注入，不要硬编码到仓库。
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# 远程 LLM Anthropic 兼容服务地址。
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.deepseek.com/anthropic")
# 远程 LLM 模型名。
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "qwen3.7-plus-2026-05-26")

# --- 本地 vLLM OpenAI 兼容接口配置（LLM_MODE=local 时启用） ---
# Planner/Executor 模型的 OpenAI 兼容服务地址。
PLANNER_EXECUTOR_BASE_URL = os.getenv("PLANNER_EXECUTOR_BASE_URL", "http://127.0.0.1:8000/v1")
# Planner/Executor 模型服务名。
PLANNER_EXECUTOR_MODEL = os.getenv("PLANNER_EXECUTOR_MODEL", "sining")
# Manager/Reviewer/Compression 模型的 OpenAI 兼容服务地址。
MANAGER_REVIEWER_BASE_URL = os.getenv("MANAGER_REVIEWER_BASE_URL", "http://127.0.0.1:8001/v1")
# Manager/Reviewer/Compression 模型服务名。
MANAGER_REVIEWER_MODEL = os.getenv("MANAGER_REVIEWER_MODEL", "qwen")
# LLM HTTP 请求超时时间，单位秒。
LLM_TIMEOUT_SECONDS = 120
# Planner 最多工具规划/调用轮数。
PLANNER_MAX_STEPS = 3
# 上下文管理器最大输出 token 数。
MAX_TOKENS = 8192 // 2
CONTEXT_MANAGER_MAX_TOKENS = MAX_TOKENS // 2
# 会话压缩器最大输出 token 数；需足够容纳 thinking 模型的最终 JSON。
COMPRESSION_MAX_TOKENS = MAX_TOKENS // 2
# 参考信息整理器最大输出 token 数。
INFO_REFINER_MAX_TOKENS = MAX_TOKENS // 2
# Planner 单次最大输出 token 数。
PLANNER_MAX_TOKENS = MAX_TOKENS // 2
# Executor 最终回答最大输出 token 数。
EXECUTOR_MAX_TOKENS = MAX_TOKENS // 2
# Reviewer 审查/反馈最大输出 token 数；完整上下文审核时需给 thinking 模型留出最终 JSON 空间。
REVIEWER_MAX_TOKENS = MAX_TOKENS // 2
# 会话标题生成最大输出 token 数。
SESSION_TITLE_MAX_TOKENS = MAX_TOKENS // 2

# --- 模型采样温度配置 ---
# 温度越低，输出越稳定；温度越高，输出越发散。可通过同名环境变量覆盖。
# 上下文/记忆抽取温度。
CONTEXT_MANAGER_TEMPERATURE = _get_float_env("CONTEXT_MANAGER_TEMPERATURE", 0.1)
# 会话压缩温度。
COMPRESSION_TEMPERATURE = _get_float_env("COMPRESSION_TEMPERATURE", 0.1)
# Planner 规划/工具选择温度。
PLANNER_TEMPERATURE = _get_float_env("PLANNER_TEMPERATURE", 0.4)
# 参考信息整理温度。
INFO_REFINER_TEMPERATURE = _get_float_env("INFO_REFINER_TEMPERATURE", 0.1)
# Executor 正式回答温度。
EXECUTOR_TEMPERATURE = _get_float_env("EXECUTOR_TEMPERATURE", 0.3)
# Reviewer 审核温度。
REVIEWER_TEMPERATURE = _get_float_env("REVIEWER_TEMPERATURE", 0.1)
# 审核不通过后重生成温度。
REGENERATION_TEMPERATURE = _get_float_env("REGENERATION_TEMPERATURE", 0.45)
# 会话标题生成温度。
SESSION_TITLE_TEMPERATURE = _get_float_env("SESSION_TITLE_TEMPERATURE", 0.1)

# --- 健康管理工作流配置 ---
# 触发会话上下文压缩的近似字符/token 阈值。
CONTEXT_COMPRESS_TOKEN_LIMIT = MAX_TOKENS * 2
# 前端/事件追踪中上下文展示的字符截断上限。
TRACE_CONTEXT_CHAR_LIMIT = 900
# 记忆抽取追踪内容的字符截断上限。
MEMORY_TRACE_CHAR_LIMIT = MAX_TOKENS
# 个性化记忆检索条数上限。
MEMORY_RETRIEVAL_LIMIT = 5
# 单条个性化记忆注入时的字符截断上限。
MEMORY_ITEM_CHAR_LIMIT = 1000
# 单条压缩摘要注入时的字符截断上限。
SUMMARY_ITEM_CHAR_LIMIT = 2048
# 短期记忆默认保留天数。
SHORT_TERM_MEMORY_DAYS = 7
# 待确认记忆候选列表返回/处理上限。
MEMORY_CANDIDATE_LIMIT = 30
# 工具结果注入给模型时的字符截断上限。
TOOL_RESULT_CHAR_LIMIT = 3000
# 工具调用追踪展示的字符截断上限。
TOOL_TRACE_CHAR_LIMIT = 500
# 系统保留用户名，避免普通用户注册或冒用。
SYSTEM_USERNAMES = {"admin", "root", "system"}

# --- 管理员账号配置 ---
# 默认管理员账号。首次启动时如果不存在会自动创建；密码建议通过环境变量覆盖。
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@sining.local")
ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "Admin@123456")

# --- 安全配置 ---
# JWT 签名密钥，生产环境应替换为安全随机值并从环境变量读取。
SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
# JWT 签名算法。
ALGORITHM = "HS256"
# 访问令牌过期时间，单位分钟。
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 

# --- 文件配置 ---
# 上传文件保存目录。嵌入其他 FastAPI 应用时不能依赖进程工作目录。
UPLOAD_DIR = os.getenv("UPLOAD_DIR", str(_RUNTIME_DIR / "uploads"))
# 文件元数据也放入运行目录，避免修改交接包源码目录。
METADATA_FILE = os.getenv("METADATA_FILE", str(_RUNTIME_DIR / "file_metadata.json"))
# 文件/附件文本进入上下文前的最大字符长度。
MAX_TEXT_LENGTH = 3000
# markitdown 文档提取超时时间，单位秒。
MARKITDOWN_EXTRACT_TIMEOUT_SECONDS = int(os.getenv("MARKITDOWN_EXTRACT_TIMEOUT_SECONDS", "120"))
# markitdown 提取产物预留目录。
MARKITDOWN_OUTPUT_DIR = os.getenv(
    "MARKITDOWN_OUTPUT_DIR", str(_RUNTIME_DIR / "markitdown_output")
)
# 视觉模型 OpenAI 兼容接口配置；API key 必须通过环境变量注入，不能写入仓库。
VISION_API_BASE_URL = os.getenv("VISION_API_BASE_URL", "")
VISION_API_MODEL = os.getenv("VISION_API_MODEL", "qwen3-vl-flash")
VISION_API_KEY = os.getenv("VISION_API_KEY", "")
VISION_API_TIMEOUT_SECONDS = int(os.getenv("VISION_API_TIMEOUT_SECONDS", "30"))

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(MARKITDOWN_OUTPUT_DIR, exist_ok=True)

# --- 🔥 新增：邮箱配置 (请修改为您的真实配置) ---
class EmailSettings(BaseModel):
    MAIL_USERNAME: str = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD: str = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM: str = os.getenv("MAIL_FROM", "noreply@example.com")
    MAIL_PORT: int = int(os.getenv("MAIL_PORT", "465"))
    MAIL_SERVER: str = os.getenv("MAIL_SERVER", "smtp.qq.com")
    # 是否启用 STARTTLS；SSL 端口 465 通常关闭。
    MAIL_STARTTLS: bool = os.getenv("MAIL_STARTTLS", "false").lower() == "true"
    # 是否启用 SSL/TLS 加密连接。
    MAIL_SSL_TLS: bool = os.getenv("MAIL_SSL_TLS", "true").lower() == "true"
    # 是否使用账号密码登录 SMTP 服务。
    USE_CREDENTIALS: bool = True
    # 是否校验 SMTP 服务端证书。
    VALIDATE_CERTS: bool = True

# 邮件配置实例，供邮件工具模块直接导入使用。
email_conf = EmailSettings()

# --- Exa api 配置 ---
# Exa 搜索服务 API Key。
EXA_API_KEY = os.getenv("EXA_API_KEY", "")
# 单次 Exa 搜索返回结果数。
EXA_NUM_RESULTS = 3
# 单条 Exa 搜索结果内容注入上下文前的字符截断上限。
EXA_CONTENT_CHAR_LIMIT = 500


# --- 正式考纲数据 ---
OFFICIAL_EXAM_DATA_DIR = Path(
    os.getenv("OFFICIAL_EXAM_DATA_DIR", str(_BACKEND_DIR / "data" / "official_exam_2025"))
).resolve()

# --- Knowledge Atlas delivery ---
# Atlas assets are intentionally isolated from document RAG source files.  Missing
# assets only disable Atlas endpoints; they never block application startup.
KNOWLEDGE_ATLAS_ENABLED = os.getenv("KNOWLEDGE_ATLAS_ENABLED", "true").lower() != "false"
KNOWLEDGE_ATLAS_ASSET_VERSION = os.getenv("KNOWLEDGE_ATLAS_ASSET_VERSION", "2026-07-18")
KNOWLEDGE_ATLAS_DATA_ROOT = Path(
    os.getenv(
        "KNOWLEDGE_ATLAS_DATA_ROOT",
        str(
            _BACKEND_DIR
            / "knowledge_atlas_assets"
            / KNOWLEDGE_ATLAS_ASSET_VERSION
            / "backend_delivery"
        ),
    )
).resolve()
KNOWLEDGE_ATLAS_VIDEO_ROOT = Path(
    os.getenv(
        "KNOWLEDGE_ATLAS_VIDEO_ROOT",
        str(_BACKEND_DIR / "knowledge_atlas_runtime" / "video"),
    )
).resolve()
_knowledge_atlas_contract_env = os.getenv("KNOWLEDGE_ATLAS_CONTRACT_PATH", "")
KNOWLEDGE_ATLAS_CONTRACT_PATH = (
    Path(_knowledge_atlas_contract_env).resolve()
    if _knowledge_atlas_contract_env
    else (
        _BACKEND_DIR
        / "knowledge_atlas_contracts"
        / KNOWLEDGE_ATLAS_ASSET_VERSION
        / "manifest.json"
    ).resolve()
)

# --- Embedding Model ---
# Embedding/RAG 开关：disabled=禁用（本地开发默认，不加载 Embedding 模型，检索返回空）；
# enabled=加载本地 Embedding 模型并启用知识库检索。
EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "disabled")

class EmbeddingConfig:
    # Index identity is stable, while the runtime model path must be provided
    # explicitly when EMBEDDING_MODE=enabled.  This avoids both implicit model
    # downloads and the former Linux-only hard-coded path.
    EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "Qwen/Qwen3-Embedding-4B")
    EMBEDDING_MODEL_PATH = os.getenv("EMBEDDING_MODEL_PATH", "").strip()
    EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "2560"))
    # Compatibility field used by status payloads and older manifest readers.
    EMBEDDING_MODEL = EMBEDDING_MODEL_ID
    
    # 向量库根目录。
    BASE_DIR = os.getenv("VDB_STORE_ROOT", str(_RUNTIME_DIR / "vdb_store"))
    
    # 知识库源文件目录。
    DATA_SOURCE_PATH = os.getenv(
        "KNOWLEDGE_DATA_SOURCE_PATH", str(_BACKEND_DIR / "data")
    )

    # 公共知识库源文件目录；默认沿用历史知识库目录，避免破坏已有公共知识。
    PUBLIC_DATA_SOURCE_PATH = DATA_SOURCE_PATH

    # 公共知识库向量库目录；默认沿用历史向量库目录。
    PUBLIC_INDEX_DIR = os.path.join(BASE_DIR, "indexes")

    # 用户个人知识库源文件根目录，内部按 user_id 分目录隔离。
    USER_DATA_ROOT = os.getenv(
        "USER_KNOWLEDGE_DATA_ROOT", str(_RUNTIME_DIR / "user_knowledge" / "data")
    )

    # 用户个人知识库向量库根目录，内部按 user_id 分目录隔离。
    USER_INDEX_ROOT = os.getenv(
        "USER_KNOWLEDGE_INDEX_ROOT", str(_RUNTIME_DIR / "user_knowledge" / "indexes")
    )
    
    # 文本切块大小。
    CHUNK_SIZE = 500
    # 相邻文本块重叠字符数。
    CHUNK_OVERLAP = 50
    # 默认检索 Top-K 数量。
    TOP_K = 5
    # 向量相似度过滤阈值。
    SIMILARITY_THRESHOLD = 0.6
