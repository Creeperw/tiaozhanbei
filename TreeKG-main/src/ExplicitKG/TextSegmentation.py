import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
# from docx import Document                         # ← DOCX 模式（已注释）
# from docx.text.paragraph import Paragraph         # ← DOCX 模式（已注释）
from tqdm import tqdm
from log_utils import setup_stage_logger
from config_utils import get_output_dir

# ===== 配置加载（相对 config.yaml 解析 include）=====


def _load_yaml(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_additional_configs(include_files: List[str], base_dir: Path) -> dict:
    merged: Dict[str, Any] = {}
    for rel in include_files or []:
        rel_str = str(rel)
        inc_path = Path(rel_str)
        if not inc_path.is_absolute():
            inc_path = (base_dir / rel_str).resolve()
        # 兼容写成 "config/xxx.yaml" 的情况：退化为同目录查找
        if not inc_path.exists() and rel_str.startswith("config/"):
            inc_path = (base_dir / rel_str.split("/", 1)[1]).resolve()
        if not inc_path.exists():
            raise FileNotFoundError(f"找不到包含文件：{inc_path}")
        merged.update(_load_yaml(inc_path))
    return merged


# 脚本所在目录：src/ExplicitKG
script_dir = Path(__file__).resolve().parent
src_dir = script_dir.parent
layer_output_dir = get_output_dir("explicit")
legacy_output_dir = script_dir / "output"

# 主配置：src/ExplicitKG/config/config.yaml
config_file = script_dir / "config" / "config.yaml"
config_dir = config_file.parent  # = src/ExplicitKG/config

# 读取主配置并合并 include
config = _load_yaml(config_file)
additional = _load_additional_configs(config.get("include_files", []), base_dir=config_dir)
config.update(additional)

# 提取子配置
TextSegConfig: Dict[str, Any] = config["TextSegConfig"]

# ===== 日志 =====
logger = setup_stage_logger("text_segmentation", layer_output_dir, console_level=logging.INFO)

# =========================
# 正则与工具
# =========================

HAS_LETTER_RE = re.compile(r"[一-龥A-Za-z]")

# ── MD 模式正则 ──
MD_CHAPTER_RE = re.compile(TextSegConfig["MD_CHAPTER_PATTERN"])
MD_SECTION_RE = re.compile(TextSegConfig["MD_SECTION_PATTERN"])
MD_CHINESE_NUM_RE = re.compile(TextSegConfig["MD_CHINESE_NUM_PATTERN"])
MD_PAREN_NUM_RE = re.compile(TextSegConfig["MD_PAREN_NUM_PATTERN"])
MD_POINT_RE = re.compile(TextSegConfig["MD_POINT_PATTERN"])
MD_APPENDIX_RE = re.compile(TextSegConfig["MD_APPENDIX_PATTERN"])

# ── DOCX 模式正则（保留）──
# CHAPTER_RE = re.compile(TextSegConfig["CHAPTER_PATTERN"])
# SECTION_RE = re.compile(TextSegConfig["SECTION_PATTERN"])
# SUBSECTION_RE = re.compile(TextSegConfig["SUBSECTION_PATTERN"])
# POINT_RE = re.compile(TextSegConfig["POINT_PATTERN"])

CN_NUM_MAP = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
}


def cn2int(s: str) -> int:
    s = (s or "").strip()
    if not s:
        return 1
    if s.isdigit():
        return int(s)
    total, tmp, seen = 0, 0, False
    for ch in s:
        if ch == "百":
            total = (total if total else 1) * 100
            tmp = 0
            seen = True
        elif ch == "十":
            total += (tmp if tmp else 1) * 10
            tmp = 0
            seen = True
        else:
            v = CN_NUM_MAP.get(ch)
            if v is None:
                try:
                    return int(s)
                except Exception:
                    return 1
            tmp = v
            seen = True
    if seen:
        total += tmp
        return total or 1
    try:
        return int(s)
    except Exception:
        return 1


def _normalize_spaces(text: str) -> str:
    if not text:
        return ""
    if TextSegConfig["NORMALIZE_SPACES"]:
        for ch in TextSegConfig["SPACE_SUBSTITUTIONS"]:
            text = text.replace(ch, " ")
        text = re.sub(r"\s{2,}", " ", text)
    return text


def clean_title(t: str) -> str:
    """去尾部点线+页码、两端空白/冒号；压缩奇异空格。"""
    if not t:
        return ""
    t = _normalize_spaces(t)
    if TextSegConfig["REMOVE_TRAILING_PAGE_NO"]:
        t = re.sub(r"[\.·・—\-＿\s　]*\d+\s*$", "", t)
    if TextSegConfig["STRIP_COLONS"]:
        t = t.strip(" ：:　\t")
    return t.strip()


# ===== DOCX 样式检测（保留，已注释）=====
# def pick_level_by_style(par: Paragraph) -> Optional[int]:
#     """从段落样式/outlineLvl/编号层级推断层级（1..4）。"""
#     try:
#         name = (par.style.name or "").strip().lower()
#         lvl = TextSegConfig["HEADING_MAP"].get(name)
#         if lvl:
#             return lvl
#     except Exception:
#         pass
#
#     try:
#         el = par._p.xpath("./w:pPr/w:outlineLvl")
#         if el:
#             lvl = int(el[0].val)
#             if 0 <= lvl <= 3:
#                 return lvl + 1
#     except Exception:
#         pass
#
#     try:
#         ilvl = par._p.xpath("./w:pPr/w:numPr/w:ilvl")
#         if ilvl:
#             lvl = int(ilvl[0].val)
#             if 0 <= lvl <= 3:
#                 return lvl + 1
#     except Exception:
#         pass
#
#     return None


# =========================
# MD 模式：标题分类
# =========================

# 标题模式枚举
_HEADING_CHAPTER = "CHAPTER"         # 第X章 / 绪论
_HEADING_SECTION = "SECTION_NUM"     # 第X节
_HEADING_CHINESE = "CHINESE_NUM"     # 一/二/三 ...
_HEADING_PAREN = "PAREN_NUM"         # （一）/（二）...
_HEADING_POINT = "POINT_NUM"       # 1. / 2. ... 数字点号
_HEADING_APPENDIX = "APPENDIX"       # ［附］...


def _classify_heading(text: str) -> Optional[str]:
    """根据文本内容判断标题类型（MD 模式）。"""
    text = text.strip()
    if not text:
        return None
    if MD_CHAPTER_RE.match(text):
        return _HEADING_CHAPTER
    if MD_SECTION_RE.match(text):
        return _HEADING_SECTION
    if MD_APPENDIX_RE.match(text):
        return _HEADING_APPENDIX
    if MD_PAREN_NUM_RE.match(text):
        return _HEADING_PAREN
    if MD_POINT_RE.match(text):
        return _HEADING_POINT
    if MD_CHINESE_NUM_RE.match(text):
        return _HEADING_CHINESE
    return None


def _extract_chapter_num(title: str) -> int:
    """从「第X章」或「绪论」中提取章序号。绪论返回 0。"""
    m = re.search(r"第\s*([一二三四五六七八九十百零〇0-9]+)\s*章", title)
    if m:
        return cn2int(m.group(1))
    if re.match(r"^\s*绪论\s*$", title):
        return 0
    return -1


# =========================
# MD 模式：解析主逻辑
# =========================


def parse_markdown(md_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    解析 Markdown 文件，提取层级目录结构。

    支持两种标题模式：
    1. 标准 md 标题：通过 # 数量判断层级（# = level 1, ## = level 2, ...）
    2. 全书只用 ## 的书籍（如中医学基础）：通过标题内容模式判断层级
       - 第X章 / 绪论  → Level 1
       - 第X节         → Level 2
       - 一/二/三...   → 上下文决定 Level
       - （一）/（二） → 上下文决定 Level
    """
    # 1. 读取所有行
    with md_path.open("r", encoding=TextSegConfig["ENCODING"]) as f:
        lines = f.readlines()

    # 2. 提取标题行（以 # 开头）
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)")
    raw_headings: List[Tuple[int, int, str]] = []  # (line_no, hash_count, text)

    for i, line in enumerate(lines):
        m = heading_pattern.match(line)
        if m:
            hashes = len(m.group(1))
            text = clean_title(m.group(2))
            if text and HAS_LETTER_RE.search(text):
                raw_headings.append((i, hashes, text))

    if not raw_headings:
        return [], ["未找到任何标题行（# 开头）"]

    # 3. 判断标题模式：是否所有标题都用同一级 #
    hash_levels = set(h for _, h, _ in raw_headings)
    single_hash_mode = (len(hash_levels) == 1)

    toc: List[Dict[str, Any]] = []
    warnings: List[str] = []
    # stack: [(level, pattern_type, node)]
    stack: List[Tuple[int, str, Dict[str, Any]]] = []

    # 章节/节计数器（用于自动生成 id）
    chapter_idx = 0
    section_counters: Dict[int, int] = {}  # level -> counter

    for line_no, hash_count, text in raw_headings:
        ptype = _classify_heading(text)

        if single_hash_mode:
            # 全书同级别 # → 靠内容模式区分层级
            if ptype is None:
                warnings.append(f"[无法分类] 行{line_no + 1}: {text}")
                continue

            if ptype == _HEADING_CHAPTER:
                level = 1
            elif ptype == _HEADING_SECTION:
                level = 2
            elif ptype == _HEADING_CHINESE:
                # 找最近的第X章或第X节（跳过其他 CHINESE_NUM），level = 其level + 1
                level = 2  # 默认
                for lv, pt, _ in reversed(stack):
                    if pt in (_HEADING_CHAPTER, _HEADING_SECTION):
                        level = lv + 1
                        break
            elif ptype in (_HEADING_PAREN, _HEADING_APPENDIX, _HEADING_POINT):
                # 找最近的 CHAPTER / SECTION_NUM / CHINESE_NUM 祖先
                level = 3  # 默认
                for lv, pt, _ in reversed(stack):
                    if pt in (_HEADING_CHAPTER, _HEADING_SECTION, _HEADING_CHINESE):
                        level = lv + 1
                        break
            else:
                level = hash_count  # fallback
        else:
            # 标准 md：用 # 数量判断层级
            level = min(hash_count, TextSegConfig["MAX_LEVEL"])

        # 自动生成 id
        if level == 1:
            # 判断是否为绪论（无编号的章）
            if '绪论' in text:
                node_id = "绪论"
                section_counters = {1: 0}  # 用 0 标记绪论
            else:
                chapter_num = _extract_chapter_num(text)
                if chapter_num <= 0:
                    chapter_num = len([n for n in toc if n.get("level") == 1]) + 1
                section_counters = {1: chapter_num}
                node_id = f"{chapter_num}章"
            # 重置子计数器
            for k in list(section_counters.keys()):
                if k > 1:
                    del section_counters[k]
        else:
            # 递增当前 level 的计数器，重置更深层的
            section_counters[level] = section_counters.get(level, 0) + 1
            for k in list(section_counters.keys()):
                if k > level:
                    del section_counters[k]
            # 构建 id：如 "1.1", "1.1.1"；绪论下为 "绪论.1", "绪论.1.1"
            parts = []
            for lv in sorted(section_counters.keys()):
                val = section_counters[lv]
                if lv == 1 and val == 0:
                    parts.append("绪论")
                else:
                    parts.append(str(val))
            node_id = ".".join(parts)

        node: Dict[str, Any] = {
            "level": level,
            "id": node_id,
            "title": text,
            "children": [],
        }

        # 弹出栈中 level >= 当前 level 的节点（它们不再是当前节点的祖先）
        while stack and stack[-1][0] >= level:
            stack.pop()

        if not stack:
            toc.append(node)
        else:
            stack[-1][2]["children"].append(node)

        stack.append((level, ptype, node))

    return toc, warnings


# ===== DOCX 解析（保留，已注释）=====
# def _parse_chapter_no(ch_id: str) -> int:
#     """从章节 id 里安全解析章号。"""
#     s = (ch_id or "").strip()
#     s = s.rstrip("章").strip()
#     s = s.lstrip("第").strip()
#     s = re.sub(r"\s+", "", s)
#     return cn2int(s)
#
#
# def parse_docx(docx_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
#     doc = Document(str(docx_path))
#     toc: List[Dict[str, Any]] = []
#     cur_ch = None
#     cur_sec = None
#     cur_sub = None
#     warnings: List[str] = []
#     for para in doc.paragraphs:
#         raw = (para.text or "").strip()
#         if not raw:
#             continue
#         text_norm = _normalize_spaces(raw).strip()
#         matched = False
#         lvl = pick_level_by_style(para) if TextSegConfig["USE_STYLE_FIRST"] else None
#         # ========= 样式优先分支 =========
#         if lvl == 1 and TextSegConfig["MAX_LEVEL"] >= 1:
#             m = CHAPTER_RE.match(text_norm)
#             if m:
#                 num_raw, title = m.group(1), clean_title(m.group(2))
#                 if HAS_LETTER_RE.search(title):
#                     node = {"level": 1, "id": f"{cn2int(num_raw)}章", "title": title, "children": []}
#                     toc.append(node)
#                     cur_ch, cur_sec, cur_sub = node, None, None
#                     matched = True
#         elif lvl == 2 and cur_ch and TextSegConfig["MAX_LEVEL"] >= 2:
#             m = SECTION_RE.match(text_norm)
#             if m:
#                 full, ch, sec, title = m.group(1), m.group(2), m.group(3), clean_title(m.group(4))
#                 if HAS_LETTER_RE.search(title):
#                     exp_ch = str(_parse_chapter_no(cur_ch["id"]))
#                     if ch == exp_ch:
#                         node = {"level": 2, "id": full, "title": title, "children": []}
#                         cur_ch["children"].append(node)
#                         cur_sec, cur_sub = node, None
#                         matched = True
#                     else:
#                         warnings.append(f"[节章号不一致] 期望{exp_ch}.x，实际{full} —— 段落：{text_norm}")
#         elif lvl == 3 and cur_sec and TextSegConfig["MAX_LEVEL"] >= 3:
#             m = SUBSECTION_RE.match(text_norm)
#             if m:
#                 full, title = m.group(1), clean_title(m.group(5))
#                 if HAS_LETTER_RE.search(title) and full.startswith(cur_sec["id"] + "."):
#                     node = {"level": 3, "id": full, "title": title, "children": []}
#                     cur_sec["children"].append(node)
#                     cur_sub = node
#                     matched = True
#                 else:
#                     warnings.append(f"[小节前缀不匹配] 期望前缀{cur_sec['id']}., 实际{full}")
#         elif lvl == 4 and cur_sub and TextSegConfig["MAX_LEVEL"] >= 4:
#             m = POINT_RE.match(text_norm)
#             if m:
#                 full, title = m.group(1), clean_title(m.group(6))
#                 if HAS_LETTER_RE.search(title) and full.startswith(cur_sub["id"] + "."):
#                     node = {"level": 4, "id": full, "title": title}
#                     cur_sub["children"].append(node)
#                     matched = True
#                 else:
#                     warnings.append(f"[知识点前缀不匹配] 期望前缀{cur_sub['id']}., 实际{full}")
#         if matched:
#             continue
#         # ========= 正则兜底分支 =========
#         if not TextSegConfig["ENABLE_REGEX_FALLBACK"]:
#             continue
#         m = CHAPTER_RE.match(text_norm)
#         if m and TextSegConfig["MAX_LEVEL"] >= 1:
#             num_raw, title = m.group(1), clean_title(m.group(2))
#             if HAS_LETTER_RE.search(title):
#                 node = {"level": 1, "id": f"{cn2int(num_raw)}章", "title": title, "children": []}
#                 toc.append(node)
#                 cur_ch, cur_sec, cur_sub = node, None, None
#             continue
#         m = SECTION_RE.match(text_norm)
#         if m and cur_ch and TextSegConfig["MAX_LEVEL"] >= 2:
#             full, ch, sec, title = m.group(1), m.group(2), m.group(3), clean_title(m.group(4))
#             if HAS_LETTER_RE.search(title):
#                 exp_ch = str(_parse_chapter_no(cur_ch["id"]))
#                 if ch == exp_ch:
#                     node = {"level": 2, "id": full, "title": title, "children": []}
#                     cur_ch["children"].append(node)
#                     cur_sec, cur_sub = node, None
#                 else:
#                     warnings.append(f"[节章号不一致] 期望{exp_ch}.x，实际{full}")
#             continue
#         m = SUBSECTION_RE.match(text_norm)
#         if m and cur_sec and TextSegConfig["MAX_LEVEL"] >= 3:
#             full, title = m.group(1), clean_title(m.group(5))
#             if HAS_LETTER_RE.search(title) and full.startswith(cur_sec["id"] + "."):
#                 node = {"level": 3, "id": full, "title": title, "children": []}
#                 cur_sec["children"].append(node)
#                 cur_sub = node
#             else:
#                 warnings.append(f"[小节前缀不匹配] 期望前缀{cur_sec['id']}., 实际{full}")
#             continue
#         m = POINT_RE.match(text_norm)
#         if m and cur_sub and TextSegConfig["MAX_LEVEL"] >= 4:
#             full, title = m.group(1), clean_title(m.group(6))
#             if HAS_LETTER_RE.search(title) and full.startswith(cur_sub["id"] + "."):
#                 node = {"level": 4, "id": full, "title": title}
#                 cur_sub["children"].append(node)
#             else:
#                 warnings.append(f"[知识点前缀不匹配] 期望前缀{cur_sub['id']}., 实际{full}")
#             continue
#     return toc, warnings


# =========================
# I/O & CLI
# =========================


def save_toc(toc: List[Dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding=TextSegConfig["ENCODING"]) as f:
        json.dump(toc, f, ensure_ascii=False, indent=2)
    logger.debug("Saved TOC json: %s, root_count=%s", out_path.resolve(), len(toc))


def save_warnings(warnings: List[str], warn_path: Path) -> None:
    if not TextSegConfig["SAVE_WARNINGS_FILE"] or not warnings:
        return
    warn_path.parent.mkdir(parents=True, exist_ok=True)
    with warn_path.open("w", encoding=TextSegConfig["ENCODING"]) as f:
        for w in warnings:
            f.write(w + "\n")
    logger.debug("Saved warnings: %s, count=%s", warn_path.resolve(), len(warnings))


def _resolve_input_path() -> Path:
    """根据 INPUT_FORMAT 解析输入文件路径。"""
    fmt = TextSegConfig.get("INPUT_FORMAT", "docx")
    if fmt == "md":
        name = TextSegConfig.get("MD_NAME", "")
        if not name:
            raise ValueError("MD 模式下必须配置 MD_NAME")
        # 先在 layer_output_dir 找，再在 legacy_output_dir 找
        p = layer_output_dir / name
        if p.exists():
            return p
        p = legacy_output_dir / name
        if p.exists():
            return p
        raise FileNotFoundError(f"未找到 md 文件：{name}（搜索路径：{layer_output_dir}、{legacy_output_dir}）")
    else:
        # DOCX 模式（保留）
        name = TextSegConfig.get("DOCX_NAME", "")
        if not name:
            raise ValueError("DOCX 模式下必须配置 DOCX_NAME")
        p = layer_output_dir / name
        if p.exists():
            return p
        p = legacy_output_dir / name
        if p.exists():
            return p
        raise FileNotFoundError(f"未找到 docx 文件：{name}")


def main():
    toc_path = layer_output_dir / TextSegConfig["TOC_NAME"]
    warn_path = layer_output_dir / TextSegConfig["WARN_NAME"]

    parser = argparse.ArgumentParser(description="解析文档目录结构（章-节-小节-知识点）—— 支持 md/docx")
    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="输入文件路径（默认根据配置文件 INPUT_FORMAT 自动查找）",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(toc_path),
        help="输出 JSON 路径（默认 output 文件夹中的 TOC_NAME）",
    )
    parser.add_argument(
        "--warn",
        type=str,
        default=str(warn_path),
        help="告警日志路径（默认 output 文件夹中的 WARN_NAME）",
    )
    parser.add_argument(
        "--loglevel",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logger.setLevel(getattr(logging, args.loglevel))

    # 解析输入路径
    if args.input:
        in_path = Path(args.input)
    else:
        in_path = _resolve_input_path()

    if not in_path.exists():
        raise FileNotFoundError(f"未找到输入文件：{in_path}")

    out_path = Path(args.out)
    warn_path = Path(args.warn)

    fmt = TextSegConfig.get("INPUT_FORMAT", "docx")
    logger.info("Start parsing. format=%s, input=%s", fmt, in_path.resolve())

    try:
        if fmt == "md":
            toc, warnings = parse_markdown(in_path)
        else:
            # DOCX 模式（保留）
            # toc, warnings = parse_docx(in_path)
            raise NotImplementedError(
                "DOCX 模式暂未启用。如需使用，请取消 TextSegmentation.py 中 parse_docx 相关代码的注释。"
            )
        logger.info("Parsed TOC roots=%s, warnings=%s", len(toc), len(warnings))
        save_toc(toc, out_path)
        save_warnings(warnings, warn_path)
    except Exception:
        logger.exception("Text segmentation failed.")
        raise

    logger.info(f"✅ 完成文本分割：{out_path.resolve()}")
    if toc:
        logger.info(f"首章：{toc[0].get('id')}  {toc[0].get('title')}")
    if warnings:
        n = len(warnings)
        logger.warning(f"共有 {n} 条格式告警（非致命）")
        for w in warnings[: TextSegConfig["WARNINGS_PRINT_TOP"]]:
            logger.warning("  - " + w)
        if n > TextSegConfig["WARNINGS_PRINT_TOP"]:
            logger.warning("  ...（其余已省略；完整内容见文件：%s）", warn_path.name)
    logger.info("Text segmentation log: %s", logger.log_path)


if __name__ == "__main__":
    main()
