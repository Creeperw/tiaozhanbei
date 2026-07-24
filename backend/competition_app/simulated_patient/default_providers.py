"""
默认 LLM 提供者 - 通义千问 API
"""

import os
from typing import List, Dict, Optional

from .llm_provider import LLMProvider

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class DefaultLLMProvider(LLMProvider):
    """DeepSeek API 实现"""

    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-chat"):
        if OpenAI is None:
            raise ImportError("请安装 openai: pip install openai")

        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY") or "sk-ad3c805d05bd4e6790ccd3db3a0b89fa"

        self.model = model
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com"
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 500,
        **kwargs
    ) -> Optional[str]:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM 调用失败: {e}")
            return None