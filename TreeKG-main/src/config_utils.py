"""
共享配置读取：从 YAML 配置获取书名，自动分目录
用法:
    from config_utils import get_book_name, get_output_dir, load_config
    book = get_book_name()                        # "中医文化学"
    out  = get_output_dir("explicit")              # output/中医文化学/01_explicit_kg/
"""

import os
from pathlib import Path
import yaml

_SELF_DIR = Path(__file__).resolve().parent
# 自适应：本文件可能在 src/、src/ExplicitKG/ 或 src/HiddenKG/
if _SELF_DIR.name in ("ExplicitKG", "HiddenKG"):
    SRC_DIR = _SELF_DIR.parent
else:
    SRC_DIR = _SELF_DIR
CONFIG_DIR = SRC_DIR / "ExplicitKG" / "config"


def load_config(yaml_name: str) -> dict:
    """加载 ExplicitKG/config/ 下的 yaml"""
    path = CONFIG_DIR / yaml_name
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 凭据和部署参数优先从环境变量读取，避免把密钥写入仓库。
    api = cfg.setdefault("APIConfig", {})
    env_overrides = {
        "API_BASE": "TREEKG_API_BASE",
        "API_KEY": "TREEKG_API_KEY",
        "MODEL_NAME": "TREEKG_MODEL_NAME",
        "TIMEOUT_SECS": "TREEKG_API_TIMEOUT",
    }
    for key, env_name in env_overrides.items():
        value = os.getenv(env_name)
        if value:
            api[key] = int(value) if key == "TIMEOUT_SECS" else value
    return cfg


def get_book_name() -> str:
    """
    从 summarize.yaml 提取书名。
    MD 模式: MD_NAME 去后缀
    JSONL 模式: JSONL_BOOK_NAME 直接返回
    """
    env_book = os.getenv("TREEKG_BOOK_NAME", "").strip()
    if env_book:
        return env_book
    cfg = load_config("summarize.yaml")
    sc = cfg.get("SummarizeConfig", {})
    fmt = sc.get("INPUT_FORMAT", "md")
    if fmt == "jsonl":
        return sc.get("JSONL_BOOK_NAME", "unknown")
    md_name = sc.get("MD_NAME", "unknown.md")
    return Path(md_name).stem  # 去掉 .md 后缀


def get_input_format() -> str:
    """返回当前输入格式: 'md' | 'docx' | 'jsonl'"""
    env_format = os.getenv("TREEKG_INPUT_FORMAT", "").strip().lower()
    if env_format:
        return env_format
    cfg = load_config("summarize.yaml")
    return cfg.get("SummarizeConfig", {}).get("INPUT_FORMAT", "md")


def get_jsonl_data_dir() -> Path:
    """JSONL 模式下的数据文件夹路径"""
    cfg = load_config("summarize.yaml")
    sc = cfg.get("SummarizeConfig", {})
    data_dir = os.getenv("TREEKG_JSONL_DATA_DIR", "").strip() or sc.get("JSONL_DATA_DIR", "")
    if data_dir:
        path = Path(data_dir)
        return path if path.is_absolute() else (SRC_DIR.parent / path).resolve()
    return SRC_DIR.parent  # 默认项目根目录


def get_output_dir(stage: str) -> Path:
    """
    stage="explicit" → output/<书名>/01_explicit_kg/
    stage="hidden"   → output/<书名>/02_hidden_kg/
    stage="merged"   → output/<书名>/03_merged/
    """
    book = get_book_name()
    mapping = {
        "explicit": f"01_explicit_kg",
        "hidden": f"02_hidden_kg",
        "merged": f"03_merged",
    }
    subdir = mapping.get(stage, stage)
    output_root = os.getenv("TREEKG_OUTPUT_ROOT", "").strip()
    root = Path(output_root).resolve() if output_root else SRC_DIR / "output"
    return root / book / subdir
