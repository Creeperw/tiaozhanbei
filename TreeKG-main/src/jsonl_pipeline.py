# -*- coding: utf-8 -*-
"""
TreeKG JSONL 双文件适配器
=========================
从 {book}.jsonl + final_knowledge_points.json 出发，
重建4层TOC → 拼接正文 → LLM摘要 → 输出 toc_with_summaries.json

用法:
  cd TreeKG-main/src
  python jsonl_pipeline.py --book 中医学基础_clean

输出: output/{book}/01_explicit_kg/toc_with_summaries.json
后续: 运行 ExplicitKG/Extraction.py → ExplicitKG/toc_graph.py → HiddenKG/main.py
"""

import argparse
import json
import logging
import re
import sys
import time
from collections import OrderedDict, defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from tqdm import tqdm

# ===== 路径设置 =====
SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR
EXPLICIT_DIR = SRC_DIR / "ExplicitKG"

# ===== 加载配置 =====
def load_yaml(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

# 合并 config.yaml + include_files
config = load_yaml(EXPLICIT_DIR / "config" / "config.yaml")
for inc in config.get("include_files", []):
    inc_path = EXPLICIT_DIR / "config" / inc
    if inc_path.exists():
        config.update(load_yaml(inc_path))

SummarizeConfig = config["SummarizeConfig"]
APIConfig = config["APIConfig"]
TextSegConfig = config["TextSegConfig"]

# ===== API 客户端（Anthropic 协议）=====
from api_client import chat_with_retry as llm_chat

# ===== 日志 =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jsonl_pipeline")

# ===== 修复后的正则（新增阿拉伯数字括号） =====
MD_CHAPTER_RE = re.compile(TextSegConfig["MD_CHAPTER_PATTERN"])
MD_SECTION_RE = re.compile(TextSegConfig["MD_SECTION_PATTERN"])
MD_CHINESE_NUM_RE = re.compile(TextSegConfig["MD_CHINESE_NUM_PATTERN"])
MD_PAREN_NUM_RE = re.compile(TextSegConfig["MD_PAREN_NUM_PATTERN"])
MD_POINT_RE = re.compile(TextSegConfig["MD_POINT_PATTERN"])
MD_APPENDIX_RE = re.compile(TextSegConfig["MD_APPENDIX_PATTERN"])
# 阿拉伯数字在中文括号中（如“（1）排便异常”）
MD_ARABIC_PAREN_RE = re.compile(
    TextSegConfig.get("MD_ARABIC_PAREN_PATTERN", r"^\s*（\d+）")
)

HEADING_CACHE: Dict[str, Optional[str]] = {}

def classify_heading(text: str) -> Optional[str]:
    """根据文本内容判断标题类型。缓存结果以加速。"""
    if text in HEADING_CACHE:
        return HEADING_CACHE[text]
    text = text.strip()
    if not text:
        HEADING_CACHE[text] = None
        return None
    if MD_CHAPTER_RE.match(text):
        HEADING_CACHE[text] = "CHAPTER"; return "CHAPTER"
    if MD_SECTION_RE.match(text):
        HEADING_CACHE[text] = "SECTION_NUM"; return "SECTION_NUM"
    if MD_APPENDIX_RE.match(text):
        HEADING_CACHE[text] = "APPENDIX"; return "APPENDIX"
    if MD_ARABIC_PAREN_RE.match(text):
        HEADING_CACHE[text] = "PAREN_NUM"; return "PAREN_NUM"
    if MD_PAREN_NUM_RE.match(text):
        HEADING_CACHE[text] = "PAREN_NUM"; return "PAREN_NUM"
    if MD_POINT_RE.match(text):
        HEADING_CACHE[text] = "POINT_NUM"; return "POINT_NUM"
    if MD_CHINESE_NUM_RE.match(text):
        HEADING_CACHE[text] = "CHINESE_NUM"; return "CHINESE_NUM"
    HEADING_CACHE[text] = None
    return None


# ===================================================================
# Phase 1: 双文件 TOC 重建
# ===================================================================

def rebuild_toc(book_dir: Path, book_name: str) -> Tuple[List[Dict], Dict[str, List[str]]]:
    """
    从 JSONL + KP 双文件重建4层 TOC。

    返回:
      toc_tree: 完整 TOC 树
      node_texts: {node_id: [chunk_text, ...]}  每个节点的正文拼接
    """
    jsonl_path = book_dir / f"{book_name}.jsonl"
    kp_path = book_dir / "final_knowledge_points.json"

    if not jsonl_path.exists():
        raise FileNotFoundError(f"找不到 JSONL: {jsonl_path}")
    if not kp_path.exists():
        raise FileNotFoundError(f"找不到 KP: {kp_path}")

    # ── 加载数据 ──
    logger.info("加载 JSONL chunks...")
    chunks = [json.loads(line) for line in open(jsonl_path, "r", encoding="utf-8")]
    logger.info(f"  加载 {len(chunks)} chunks")

    with open(kp_path, "r", encoding="utf-8") as f:
        kps = json.load(f)
    logger.info(f"  加载 {len(kps)} 知识点")

    # ── 推断章边界 ──
    lv2_order = OrderedDict()
    for c in chunks:
        l2 = c["kp_Lv2"]
        if l2 not in lv2_order:
            lv2_order[l2] = c["chunk_index"]
    sorted_lv2 = sorted(lv2_order.items(), key=lambda x: x[1])

    chapters: List[List[str]] = []
    current: List[str] = []
    for lv2, _ in sorted_lv2:
        if ("第一节" in lv2 or lv2 == "绪论") and current:
            chapters.append(current)
            current = []
        current.append(lv2)
    if current:
        chapters.append(current)

    # ── 映射 ──
    # 每个 kp_lv2 → 按 chunk_index 排序的 heading_path 列表
    lv2_to_headings: Dict[str, List[str]] = defaultdict(OrderedDict)
    for c in chunks:
        hp = c["metadata"]["heading_path"]
        l2 = c["kp_Lv2"]
        if hp and hp not in lv2_to_headings[l2]:
            lv2_to_headings[l2][hp] = c["chunk_index"]

    # ── 自动推断章标题 ──
    # 优先用 CHAPTER 型 heading_path，否则编号
    chapter_titles = []
    for idx, secs in enumerate(chapters):
        found = None
        for sec_name in secs:
            headings = lv2_to_headings.get(sec_name, {})
            for hp in headings:
                if classify_heading(hp) == "CHAPTER":
                    found = hp
                    break
            if found:
                break
        if found:
            chapter_titles.append(found)
        elif secs and secs[0] == "绪论":
            chapter_titles.append("绪论")
        else:
            chapter_titles.append(f"第{idx + 1}章")
    logger.info(f"  推断章数: {len(chapters)}, 标题: {chapter_titles[:3]}...")

    # heading_path → chunk_uids
    hp_to_chunk_uids: Dict[str, List[str]] = defaultdict(list)
    for c in chunks:
        hp = c["metadata"]["heading_path"]
        hp_to_chunk_uids[hp].append(c["chunk_uid"])

    # chunk_uid → chunk
    uid_to_chunk = {c["chunk_uid"]: c for c in chunks}

    # ── 建子树 (在 kp_lv2 内部, 节标题为 L2 虚拟根) ──
    def build_subtree(heading_list: List[str], parent_title: str = "") -> List[Dict]:
        """节内子树: L3=CHINESE_NUM, L4=PAREN_NUM/POINT_NUM/APPENDIX"""
        toc, stack, counters = [], [], {}

        # 虚拟节根节点作为 stack 锚点
        virtual_root: Dict = {"level": 2, "id": "0", "title": parent_title, "children": toc}
        stack.append((2, "SECTION_NUM", virtual_root))

        for text in heading_list:
            if text == parent_title:
                continue
            ptype = classify_heading(text)
            if ptype is None or ptype == "CHAPTER":
                continue

            # 推断层级（相对于节根 L2）
            if ptype == "SECTION_NUM":
                # 嵌套的节标题 → 当作 L3 处理
                level = 3
            elif ptype == "CHINESE_NUM":
                level = 3
            elif ptype in ("PAREN_NUM", "APPENDIX", "POINT_NUM"):
                level = 3
                for lv, pt, _ in reversed(stack):
                    if pt in ("SECTION_NUM", "CHINESE_NUM"):
                        level = lv + 1
                        break
            else:
                level = 3

            counters[level] = counters.get(level, 0) + 1
            for k in list(counters.keys()):
                if k > level:
                    del counters[k]
            node_id = ".".join(str(counters.get(l, 0)) for l in sorted(counters))
            node = {
                "level": level,
                "id": node_id,
                "title": text,
                "children": [],
            }
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack[-1][2]["children"].append(node)
            stack.append((level, ptype, node))
        return toc

    # ── 组装完整树 ──
    toc_tree: List[Dict] = []
    for ch_idx, ch_sections in enumerate(chapters):
        ch_title = chapter_titles[ch_idx]
        ch_node = {
            "level": 1,
            "id": str(ch_idx + 1) if ch_title != "绪论" else "绪论",
            "title": ch_title,
            "children": [],
        }
        for sec_name in ch_sections:
            heading_dict = lv2_to_headings.get(sec_name, {})
            # 过滤: 去掉与章标题同名、与节标题同名、CHAPTER 型的 heading
            sorted_hps = [
                hp for hp, _ in sorted(heading_dict.items(), key=lambda x: x[1])
                if hp != sec_name and hp != ch_title and classify_heading(hp) != "CHAPTER"
            ]
            subtree = build_subtree(sorted_hps, parent_title=sec_name)
            sec_node = {
                "level": 2,
                "id": sec_name,
                "title": sec_name,
                "children": subtree,
            }
            ch_node["children"].append(sec_node)
        toc_tree.append(ch_node)

    # ── 收集每个节点的正文 ──
    node_texts: Dict[str, List[str]] = {}

    # 展开 TOC 并匹配 heading_path
    def collect_texts(nodes, path_prefix=""):
        for n in nodes:
            title = n["title"]
            node_key = f"{path_prefix}/{title}" if path_prefix else title

            # 找匹配的 chunk texts
            texts = []
            # 精确匹配
            if title in hp_to_chunk_uids:
                for uid in hp_to_chunk_uids[title]:
                    if uid in uid_to_chunk:
                        texts.append(uid_to_chunk[uid]["text"])
            # 模糊匹配 (heading_path 包含此标题，或标题包含 heading_path)
            if not texts:
                matching_hps = [
                    hp for hp in hp_to_chunk_uids
                    if title in hp or hp in title
                ]
                for hp in matching_hps[:5]:  # 限制模糊匹配数
                    for uid in hp_to_chunk_uids[hp]:
                        if uid in uid_to_chunk and uid_to_chunk[uid]["text"] not in texts:
                            texts.append(uid_to_chunk[uid]["text"])

            if texts:
                node_texts[node_key] = texts

            collect_texts(n.get("children", []), node_key)

    collect_texts(toc_tree)

    # 统计
    total_nodes = 0
    def count_nodes(nodes):
        nonlocal total_nodes
        for n in nodes:
            total_nodes += 1
            count_nodes(n.get("children", []))
    count_nodes(toc_tree)

    lvl_counter = Counter()
    def count_levels(nodes):
        for n in nodes:
            lvl_counter[n["level"]] += 1
            count_levels(n.get("children", []))
    count_levels(toc_tree)

    logger.info(f"  TOC 总节点: {total_nodes}")
    logger.info(f"  层级分布: {dict(sorted(lvl_counter.items()))}")
    logger.info(f"  有正文的节点: {len(node_texts)}")

    return toc_tree, node_texts


# ===================================================================
# Phase 2: LLM 摘要 (复用 Summarize.py 逻辑)
# ===================================================================

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = s.replace("　", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def split_chunks(text: str, max_len: int, overlap: int) -> List[str]:
    t = text.strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    chunks_list, i = [], 0
    while i < len(t):
        chunks_list.append(t[i:i + max_len])
        if i + max_len >= len(t):
            break
        i = i + max_len - overlap
    return chunks_list


def _call_llm(prompt: str) -> str:
    """调用 LLM，返回回答文本"""
    return llm_chat(prompt, max_tokens=2048,
                    retries=SummarizeConfig["RETRY_ATTEMPTS"],
                    backoff_base=SummarizeConfig["RETRY_BACKOFF_BASE"])


def summarize_leaf_text(raw_text: str) -> str:
    if not raw_text.strip():
        return ""
    parts = split_chunks(raw_text, SummarizeConfig["MAX_CHARS"], SummarizeConfig["CHUNK_OVERLAP"])
    summaries = []
    for ck in parts:
        prompt = SummarizeConfig["LEAF_PROMPT"].format(
            target_len=SummarizeConfig["TARGET_SUMMARY_LEN"], content=ck
        )
        summaries.append(_call_llm(prompt))
    if len(summaries) == 1:
        return summaries[0]
    merged = "\n\n".join(summaries)
    prompt = SummarizeConfig["AGG_PROMPT"].format(
        target_len=SummarizeConfig["TARGET_SUMMARY_LEN"], content=merged
    )
    return _call_llm(prompt)


def aggregate_children(children: List[Dict]) -> str:
    pieces = []
    for ch in children:
        s = (ch.get("summary") or "").strip()
        if s:
            pieces.append(f"[{ch.get('id', '')}] {s}")
    if not pieces:
        return ""
    content = "\n\n".join(pieces)
    prompt = SummarizeConfig["AGG_PROMPT"].format(
        target_len=SummarizeConfig["TARGET_SUMMARY_LEN"], content=content
    )
    return _call_llm(prompt)


def is_leaf(node: Dict) -> bool:
    return not node.get("children")


# ===================================================================
# Phase 3: 自底向上摘要
# ===================================================================

def run_summarization(toc_tree: List[Dict], node_texts: Dict[str, List[str]]) -> List[Dict]:
    """
    自底向上为每个 TOC 节点生成摘要。
    - 叶子节点: 拼接正文 → LLM 摘要
    - 非叶节点: 聚合子摘要 → LLM 摘要
    """
    # 打深度标签
    max_depth = 1

    def compute_depth(nodes, d=1):
        nonlocal max_depth
        for n in nodes:
            n["_depth"] = d
            max_depth = max(max_depth, d)
            compute_depth(n.get("children", []), d + 1)

    compute_depth(toc_tree)
    logger.info(f"TOC 最大深度: {max_depth}")

    # 统计总节点
    total = 0
    def cnt(nodes):
        nonlocal total
        for n in nodes:
            total += 1
            cnt(n.get("children", []))
    cnt(toc_tree)

    pbar = tqdm(total=total, desc="LLM 摘要生成（分层并发）")

    def process_node(n: Dict, texts_dict: Dict[str, List[str]]):
        # 断点续跑：跳过已有有效摘要的节点
        existing = n.get("summary", "").strip()
        if existing and len(existing) > 10:
            pbar.update(1)
            return
        if is_leaf(n):
            # 拼接正文
            title = n["title"]
            texts = texts_dict.get(title, [])
            # 也尝试模糊匹配
            if not texts:
                for key, val in texts_dict.items():
                    if title in key or key.split("/")[-1] == title:
                        texts = val
                        break
            raw = "\n\n".join(texts) if texts else ""
            n["summary"] = summarize_leaf_text(raw)
        else:
            n["summary"] = aggregate_children(n.get("children", []))
        pbar.update(1)

    # 自底向上：每层并发
    def nodes_at_depth(nodes, d):
        out = []
        for n in nodes:
            if n.get("_depth") == d:
                out.append(n)
            out.extend(nodes_at_depth(n.get("children", []), d))
        return out

    with ThreadPoolExecutor(max_workers=SummarizeConfig["MAX_WORKERS"]) as pool:
        for d in range(max_depth, 0, -1):
            layer = nodes_at_depth(toc_tree, d)
            logger.info(f"  深度 {d}: {len(layer)} 个节点")
            futures = [pool.submit(process_node, n, node_texts) for n in layer]
            for fu in as_completed(futures):
                fu.result()

    pbar.close()
    return toc_tree


# ===================================================================
# Main
# ===================================================================

def main():
    parser = argparse.ArgumentParser(description="TreeKG JSONL 双文件适配器")
    parser.add_argument("--book", type=str, required=True,
                        help="书名（对应 data 目录下的文件夹名，如 中医学基础_clean）")
    parser.add_argument("--book-dir", type=str, default=None,
                        help="书籍文件夹路径（默认: 当前目录下的 {book}/）")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="输出目录（默认: src/output/{book}/01_explicit_kg/）")
    parser.add_argument("--chapter-titles", type=str, default=None,
                        help="章标题列表 JSON 文件路径（可选，用于自定义章名）")
    parser.add_argument("--skip-summarize", action="store_true",
                        help="跳过 LLM 摘要生成（仅输出 TOC 结构）")
    args = parser.parse_args()

    book_name = args.book

    # 书籍文件夹
    if args.book_dir:
        book_dir = Path(args.book_dir)
    else:
        book_dir = Path(book_name)  # 当前目录下的同名文件夹
    if not book_dir.exists():
        # 尝试 SRC_DIR 下
        book_dir = SRC_DIR / book_name
    if not book_dir.exists():
        raise FileNotFoundError(f"找不到书籍文件夹: {book_dir}")

    # 输出目录
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = SRC_DIR / "output" / book_name / "01_explicit_kg"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"书籍: {book_name}")
    logger.info(f"数据: {book_dir}")
    logger.info(f"输出: {out_dir}")

    # ── Phase 1: 重建 TOC ──
    logger.info("=" * 50)
    logger.info("Phase 1: 双文件 TOC 重建")
    toc_tree, node_texts = rebuild_toc(book_dir, book_name)

    # 写出 TOC 结构
    toc_path = out_dir / "toc_structure.json"
    with open(toc_path, "w", encoding="utf-8") as f:
        json.dump(toc_tree, f, ensure_ascii=False, indent=2)
    logger.info(f"  TOC 已写出: {toc_path}")

    if args.skip_summarize:
        logger.info("跳过摘要生成。")
        return

    # ── Phase 2: LLM 摘要 ──
    logger.info("=" * 50)
    logger.info("Phase 2: LLM 摘要生成")

    # chapter_titles 已在 rebuild_toc 中自动推断
    # 这里需要重新组织，改为传参方式
    toc_with_summaries = run_summarization(toc_tree, node_texts)

    # 清理内部字段
    def clean_node(n):
        n.pop("_depth", None)
        for c in n.get("children", []):
            clean_node(c)
    for root in toc_with_summaries:
        clean_node(root)

    out_path = out_dir / "toc_with_summaries.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(toc_with_summaries, f, ensure_ascii=False, indent=2)
    logger.info(f"  摘要结果已写出: {out_path}")

    logger.info("=" * 50)
    logger.info("完成！")
    logger.info(f"")
    logger.info(f"  后续步骤:")
    logger.info(f"  1. python ExplicitKG/Extraction.py  (需配置 IN_NAME)")
    logger.info(f"  2. python ExplicitKG/toc_graph.py")
    logger.info(f"  3. python HiddenKG/main.py")
    logger.info(f"")
    logger.info(f"  输出目录: {out_dir}")


if __name__ == "__main__":
    main()
