"""
共享配置读取：从 YAML 配置获取书名，自动分目录
用法:
    from config_utils import get_book_name, get_output_dir, load_config
    book = get_book_name()                        # "中医文化学"
    out  = get_output_dir("explicit")              # output/中医文化学/01_explicit_kg/
"""

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
        return yaml.safe_load(f) or {}


def get_book_name() -> str:
    """
    从 summarize.yaml 的 MD_NAME 提取书名（去后缀）
    返回如 "中医学基础"、"中医文化学"
    """
    cfg = load_config("summarize.yaml")
    md_name = cfg.get("SummarizeConfig", {}).get("MD_NAME", "unknown.md")
    return Path(md_name).stem  # 去掉 .md 后缀


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
    return SRC_DIR / "output" / book / subdir
