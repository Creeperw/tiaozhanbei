# -*- coding: utf-8 -*-
"""memory_retrieval 混合检索模块测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from APP.backend.memory_retrieval import rank_memories, _tokens, _BM25, _recency_score


def _memory(
    title: str,
    content: str,
    *,
    category: str = "note",
    importance: str = "normal",
    updated_at: datetime | None = None,
) -> dict:
    return {
        "id": hash(title) % 100000,
        "category": category,
        "title": title,
        "content": content,
        "importance": importance,
        "updated_at": updated_at or datetime.now(timezone.utc),
    }


class TokenizeTests(unittest.TestCase):
    def test_chinese_bigram(self):
        tokens = _tokens("四君子汤主治脾胃气虚")
        self.assertIn("四君", tokens)
        self.assertIn("君子", tokens)
        self.assertIn("子汤", tokens)
        self.assertIn("气虚", tokens)

    def test_english_words(self):
        # 与题库检索一致：空白先被去除，连续小写字母串作为一个 token
        tokens = _tokens("study TCM herbal formula")
        self.assertIn("studytcmherbalformula", tokens)

    def test_whitespace_normalized(self):
        self.assertEqual(_tokens("  四君子\n汤  "), _tokens("四君子汤"))


class BM25Tests(unittest.TestCase):
    def test_relevant_document_scores_higher(self):
        corpus = [
            _tokens("四君子汤主治脾胃气虚证"),
            _tokens("感冒的辨证分型与治法"),
            _tokens("针灸穴位定位与主治"),
        ]
        bm25 = _BM25(corpus)
        scores = bm25.scores(_tokens("四君子汤 气虚"))
        self.assertGreater(scores[0], scores[1])
        self.assertGreater(scores[0], scores[2])

    def test_empty_corpus_safe(self):
        bm25 = _BM25([])
        self.assertEqual(bm25.scores(_tokens("测试")), [])


class RecencyTests(unittest.TestCase):
    def test_newer_higher(self):
        now = datetime.now(timezone.utc)
        fresh = _recency_score(now)
        old = _recency_score(now - timedelta(days=28))
        self.assertGreater(fresh, old)

    def test_iso_string_supported(self):
        value = datetime.now(timezone.utc).isoformat()
        self.assertGreater(_recency_score(value), 0.0)

    def test_naive_datetime_supported(self):
        value = datetime.now()  # 无时区
        self.assertGreater(_recency_score(value), 0.0)

    def test_none_returns_zero(self):
        self.assertEqual(_recency_score(None), 0.0)


class RankMemoriesTests(unittest.TestCase):
    def test_empty_query_returns_recency_order(self):
        old = _memory("旧记忆", "很久以前的内容", updated_at=datetime.now(timezone.utc) - timedelta(days=30))
        fresh = _memory("新记忆", "最近的内容", updated_at=datetime.now(timezone.utc))
        result = rank_memories("", [old, fresh], top_n=5)
        self.assertEqual(result[0]["title"], "新记忆")
        self.assertIn("retrieval_score", result[0])

    def test_empty_memories(self):
        self.assertEqual(rank_memories("查询", []), [])

    def test_bm25_ranks_relevant_higher(self):
        relevant = _memory("近期薄弱点", "四君子汤与理中丸混淆，需要重点辨析脾胃气虚与虚寒", importance="important")
        irrelevant = _memory("生活安排", "明天下午三点要去医院复查", importance="normal")
        result = rank_memories("四君子汤 脾胃气虚 辨析", [irrelevant, relevant], top_n=5)
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["title"], "近期薄弱点")

    def test_irrelevant_memory_filtered_when_no_content_hit(self):
        unrelated = _memory("生活安排", "明天下午三点要去医院复查", importance="normal")
        # 查询与记忆无任何内容关联时（BM25=0 且向量通道不可用），不应注入无关记忆
        result = rank_memories("四君子汤 气虚", [unrelated], top_n=5)
        self.assertEqual(result, [])

    def test_important_memory_boosted_within_content_hits(self):
        now = datetime.now(timezone.utc)
        weak = _memory("弱记忆", "中药学 方剂组成背诵", importance="normal", updated_at=now)
        strong = _memory("重要记忆", "中药学 方剂组成 每日背诵 30 分钟", importance="important", updated_at=now - timedelta(days=1))
        result = rank_memories("中药学 方剂组成", [weak, strong], top_n=5)
        self.assertEqual(result[0]["title"], "重要记忆")

    def test_top_n_limit(self):
        items = [
            _memory(f"记忆{i}", f"方剂{i} 组成与功效" if i < 10 else "无关生活内容")
            for i in range(30)
        ]
        result = rank_memories("方剂 组成", items, top_n=3)
        self.assertLessEqual(len(result), 3)

    def test_selected_fields_preserved(self):
        item = _memory("偏好", "喜欢案例训练", category="preference")
        result = rank_memories("案例训练", [item], top_n=5)
        if result:
            self.assertEqual(result[0]["category"], "preference")
            self.assertIn("retrieval_channels", result[0])


if __name__ == "__main__":
    unittest.main()
