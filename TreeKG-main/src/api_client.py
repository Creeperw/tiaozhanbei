# -*- coding: utf-8 -*-
"""
共享 API 客户端 — Anthropic 协议（火山方舟 Agent Plan）
供 jsonl_pipeline.py / Extraction.py / HiddenKG/* 使用
"""
import os
import re
import time
import logging
import requests
import yaml
from pathlib import Path

logger = logging.getLogger("api_client")

# 配置懒加载
_config = None

def _load_config():
    global _config
    if _config is not None:
        return _config
    config_path = Path(__file__).resolve().parent / "ExplicitKG" / "config" / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    # 合并 include_files
    for inc in raw.get("include_files", []):
        inc_path = config_path.parent / inc
        if inc_path.exists():
            with open(inc_path, "r", encoding="utf-8") as f2:
                raw.update(yaml.safe_load(f2) or {})
    _config = raw
    return _config


def get_api_config():
    cfg = _load_config()
    api = cfg.get("APIConfig", {})
    base = (
        os.getenv("TREEKG_API_BASE")
        or api.get("API_BASE", "https://ark.cn-beijing.volces.com/api/plan/v1")
        or ""
    ).rstrip("/")
    url = base if base.endswith("/messages") else base + "/messages"
    return {
        "url": url,
        "key": os.getenv("TREEKG_API_KEY") or api.get("API_KEY", ""),
        "model": os.getenv("TREEKG_MODEL_NAME") or api.get("MODEL_NAME", "deepseek-v4-flash"),
        "timeout": int(os.getenv("TREEKG_API_TIMEOUT") or api.get("TIMEOUT_SECS", 120)),
    }


def chat(prompt: str, max_tokens: int = 2048) -> str:
    """
    调用 Anthropic 协议 API，返回回答文本。
    自动去除 <|end|> 标记和 <think>...</think> 块。
    """
    cfg = get_api_config()
    if not cfg["key"]:
        raise RuntimeError("未配置 API 密钥，请设置环境变量 TREEKG_API_KEY")
    headers = {
        "x-api-key": cfg["key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    resp = requests.post(cfg["url"], headers=headers, json=body, timeout=cfg["timeout"])
    if resp.status_code != 200:
        raise RuntimeError(f"API HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    text = "".join(
        b.get("text", "") for b in data.get("content", []) if isinstance(b, dict) and b.get("type") == "text"
    )
    # 去除 Agent Plan 结束标记
    text = re.sub(r"<\|end\|>", "", text)
    # 去除 <think>...</think>
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def chat_with_retry(prompt: str, max_tokens: int = 2048, retries: int = 3, backoff_base: float = 1.8) -> str:
    """带重试的 API 调用"""
    last_err = None
    for k in range(retries):
        try:
            return chat(prompt, max_tokens)
        except Exception as e:
            last_err = e
            wait = backoff_base ** k
            logger.warning("API 调用失败 (%s/%s): %s, %ss 后重试", k + 1, retries, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"API 调用彻底失败（已重试 {retries} 次）: {last_err}")
