import logging
import os
import subprocess
import sys
from pathlib import Path

from log_utils import setup_stage_logger
from config_utils import get_output_dir, get_input_format, get_book_name, get_jsonl_data_dir


BASE_PATH = Path(__file__).resolve().parents[1]
LOG_OUTPUT_DIR = get_output_dir("explicit")
logger = setup_stage_logger("explicit_main", LOG_OUTPUT_DIR, console_level=logging.INFO)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_script(script_name: str):
    try:
        logger.info("Starting script: %s", script_name)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        process = subprocess.Popen(
            [sys.executable, str(BASE_PATH / script_name)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
            env=env,
            universal_newlines=True,
            cwd=str(BASE_PATH),
        )

        for line in iter(process.stdout.readline, ""):
            print(line, end="", flush=True)

        process.wait()
        if process.returncode != 0:
            logger.error("Script failed: %s, returncode=%s", script_name, process.returncode)
            print(f"运行 {script_name} 时发生错误")
            sys.exit(1)

        logger.info("Finished script: %s", script_name)
        print(f"成功运行 {script_name}\n")
    except Exception:
        logger.exception("Script raised exception: %s", script_name)
        print(f"运行 {script_name} 时发生错误")
        sys.exit(1)


def run_jsonl_adapter():
    """运行 JSONL 双文件适配器（替代 TextSegmentation + Summarize）"""
    sys.path.insert(0, str(BASE_PATH))
    from jsonl_pipeline import rebuild_toc, run_summarization
    import json

    book_name = get_book_name()
    data_dir = get_jsonl_data_dir()
    book_dir = data_dir / book_name
    out_dir = LOG_OUTPUT_DIR

    logger.info("=" * 50)
    logger.info("JSONL 模式: 双文件 TOC 重建 + LLM 摘要")
    logger.info("  书籍: %s", book_name)
    logger.info("  数据: %s", book_dir)
    logger.info("  输出: %s", out_dir)

    # Phase 1: 重建 TOC
    logger.info("Phase 1/2: 双文件 TOC 重建...")
    toc_tree, node_texts = rebuild_toc(book_dir, book_name)

    toc_path = out_dir / "toc_structure.json"
    toc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(toc_path, "w", encoding="utf-8") as f:
        json.dump(toc_tree, f, ensure_ascii=False, indent=2)
    logger.info("  TOC 已写出: %s", toc_path)

    # Phase 2: LLM 摘要（支持断点续跑）
    out_path = out_dir / "toc_with_summaries.json"
    if out_path.exists():
        logger.info("Phase 2/2: 检测到已有摘要文件，跳过已完成节点...")
        with open(out_path, "r", encoding="utf-8") as f:
            toc_tree = json.load(f)
        # 只对没有 summary 的节点跑 LLM
        run_summarization(toc_tree, node_texts)
        toc_with_summaries = toc_tree
    else:
        logger.info("Phase 2/2: LLM 摘要生成...")
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
    logger.info("  摘要已写出: %s", out_path)
    logger.info("=" * 50)


def main():
    fmt = get_input_format()
    print(f"工作目录：{BASE_PATH}")
    print(f"输入格式：{fmt}")
    logger.info("ExplicitKG pipeline started. fmt=%s, cwd=%s", fmt, BASE_PATH)

    if fmt == "jsonl":
        # JSONL 模式: 适配器 → Extraction → toc_graph
        run_jsonl_adapter()
        scripts = [
            "ExplicitKG/Extraction.py",
            "ExplicitKG/toc_graph.py",
        ]
    else:
        # MD/DOCX 模式: 原有流程
        scripts = [
            "ExplicitKG/TextSegmentation.py",
            "ExplicitKG/Summarize.py",
            "ExplicitKG/Extraction.py",
            "ExplicitKG/toc_graph.py",
        ]

    for script in scripts:
        print(f"正在执行 {script}...")
        run_script(script)

    logger.info("ExplicitKG pipeline finished. log=%s", logger.log_path)


if __name__ == "__main__":
    main()
