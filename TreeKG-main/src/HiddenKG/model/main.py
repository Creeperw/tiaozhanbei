# -*- coding: utf-8 -*-
"""BERT 中文编码器封装（原仓库缺失，补回）。

接口：get_encoder(model_name, device, batch_size, max_length, use_amp)
      -> encoder.encode_texts(texts: list[str]) -> np.ndarray (N x D)
"""
from __future__ import annotations

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer


class BertEncoder:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 16,
                 max_length: int = 512, use_amp: bool = False) -> None:
        self.device = device if device.startswith("cuda") and torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(self.device).eval()
        self.batch_size = max(1, int(batch_size))
        self.max_length = int(max_length)
        self.use_amp = bool(use_amp) and self.device.startswith("cuda")

    @torch.no_grad()
    def encode_texts(self, texts: list[str]) -> np.ndarray:
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            encoded = self.tokenizer(
                batch, padding=True, truncation=True,
                max_length=self.max_length, return_tensors="pt",
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.autocast("cuda", enabled=self.use_amp):
                outputs = self.model(**encoded)
            mask = encoded["attention_mask"].unsqueeze(-1).float()
            pooled = (outputs.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            vectors.append(pooled.cpu().numpy())
        if not vectors:
            return np.zeros((0, 768), dtype="float32")
        return np.vstack(vectors).astype("float32")


def get_encoder(model_name: str, device: str = "cpu", batch_size: int = 16,
                max_length: int = 512, use_amp: bool = False) -> BertEncoder:
    return BertEncoder(model_name, device=device, batch_size=batch_size,
                       max_length=max_length, use_amp=use_amp)