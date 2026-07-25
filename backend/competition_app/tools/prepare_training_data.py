"""Build auditable qualification-paper and simulated-patient data assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable


QUESTION_START = re.compile(r"(?:^|\n)\s*(\d{1,4})[、．.]\s*")
OPTION_START = re.compile(r"(?<![A-Za-z0-9])([A-F])[、．.]?\s*")


def parse_markdown_questions(markdown: str) -> list[dict]:
    matches = list(QUESTION_START.finditer(markdown))
    questions: list[dict] = []
    for index, match in enumerate(matches):
        chunk = markdown[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(markdown)]
        option_matches = list(OPTION_START.finditer(chunk))
        if option_matches:
            stem = chunk[:option_matches[0].start()].strip()
            options = [
                {"option_id": option.group(1), "content": chunk[option.end():option_matches[next_index + 1].start() if next_index + 1 < len(option_matches) else len(chunk)].strip()}
                for next_index, option in enumerate(option_matches)
            ]
        else:
            stem, options = chunk.strip(), []
        stem = re.sub(r"\s+", " ", stem).strip()
        options = [item for item in options if item["content"]]
        questions.append({"number": int(match.group(1)), "question_content": stem, "options": options})
    return questions


def validate_question(question: dict, *, require_answer: bool = True) -> str:
    if not str(question.get("question_content") or "").strip():
        return "missing_stem"
    if not isinstance(question.get("options"), list) or not question["options"]:
        return "missing_options"
    answer = question.get("answer")
    if require_answer and (not isinstance(answer, list) or not answer):
        return "missing_answer"
    option_ids = {str(item.get("option_id")) for item in question["options"] if isinstance(item, dict)}
    if len(option_ids) != len(question["options"]):
        return "duplicate_option_id"
    if answer and not set(map(str, answer)).issubset(option_ids):
        return "answer_not_in_options"
    content = str(question.get("question_content") or "")
    if "<table" in content.lower() or len(question["options"]) > 8:
        return "unparsed_compound_question"
    if any("答案" in str(item.get("content") or "") or "解析" in str(item.get("content") or "") for item in question["options"]):
        return "answer_leak_in_option"
    return ""


def build_clinical_cases(records: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        if record.get("题目大来源") != "CMB-Clin" or record.get("题型") != "临床案例问答":
            continue
        case_id = str((record.get("source") or {}).get("case_id") or "").strip()
        if case_id:
            grouped[case_id].append(record)

    cases, rejected = [], []
    for case_id, questions in grouped.items():
        diagnosis = next((item for item in questions if "诊断" in str(item.get("题目内容") or "") and "治疗" not in str(item.get("题目内容") or "")), None)
        if diagnosis is None:
            rejected.append({"case_id": case_id, "reason": "missing_diagnosis_question"})
            continue
        answer = str(diagnosis.get("题目答案") or "")
        match = re.search(r"诊断[：:]\s*([^。\n；;]+)", answer)
        diagnosis_name = match.group(1).strip() if match else ""
        if not diagnosis_name:
            rejected.append({"case_id": case_id, "reason": "missing_structured_diagnosis"})
            continue
        visible_content = str(diagnosis.get("题目内容") or "")
        if diagnosis_name and diagnosis_name in visible_content:
            rejected.append({"case_id": case_id, "reason": "diagnosis_leaked_in_visible_case"})
            continue
        treatment = next((item for item in questions if "治疗" in str(item.get("题目内容") or "")), {})
        differential = next((item for item in questions if "鉴别" in str(item.get("题目内容") or "")), {})
        source = diagnosis.get("source") or {}
        cases.append({
            "题目id": case_id,
            "匹配状态": "aggregated_cmb_clin",
            "题目大来源": "CMB-Clin",
            "题目章节来源": diagnosis.get("题目章节来源", "临床案例"),
            "题型": "临床案例问答",
            "题号": 1,
            "题目内容": diagnosis.get("题目内容", ""),
            "题目答案": diagnosis_name,
            "诊断依据": answer,
            "标签": diagnosis.get("标签", []),
            "kp_ids": sorted({str(kp) for item in questions for kp in item.get("kp_ids", []) if str(kp)}),
            "source": {"case_id": case_id, "source_file": source.get("source_file", "")},
            "补充资料": {
                "treatment": str(treatment.get("题目答案") or ""),
                "differential": str(differential.get("题目答案") or ""),
            },
        })
    return cases, rejected


def _reference_index(reference_path: Path) -> dict[str, dict]:
    if not reference_path.is_file():
        return {}
    indexed = {}
    for item in json.loads(reference_path.read_text(encoding="utf-8")):
        content = re.sub(r"\s+", "", str(item.get("question_content") or ""))
        if content and item.get("answer"):
            indexed.setdefault(content, item)
            if len(content) >= 20:
                indexed.setdefault(content[:20], item)
    return indexed


def _paper_id(exam_id: str, source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    return f"{exam_id}-{digest}"


def _ungraded_options(options: list[dict]) -> list[dict]:
    normalized = []
    for index, option in enumerate(options, start=1):
        option_id = chr(64 + index) if index <= 26 else f"选项{index}"
        content = str(option.get("content") or "").strip()
        if content:
            normalized.append({"option_id": option_id, "content": content})
    return normalized


def build_qualification_papers(source_root: Path, output_root: Path, reference_path: Path) -> dict:
    reference = _reference_index(reference_path)
    papers_dir = output_root / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    catalog, audits = {"schema_version": "1.0", "exams": [], "papers": []}, []
    for exam_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        exam_id = hashlib.sha1(exam_dir.name.encode("utf-8")).hexdigest()[:12]
        catalog["exams"].append({"exam_id": exam_id, "name": exam_dir.name})
        for markdown_path in sorted(exam_dir.rglob("full.md")):
            raw_questions = parse_markdown_questions(markdown_path.read_text(encoding="utf-8", errors="replace"))
            published, rejected = [], defaultdict(int)
            for raw in raw_questions:
                normalized = re.sub(r"\s+", "", raw["question_content"])
                reference_item = reference.get(normalized, reference.get(normalized[:20], {}))
                question = {
                    "question_id": f"{_paper_id(exam_id, markdown_path)}-q{raw['number']:04d}",
                    "question_type": "single_choice" if len(raw["options"]) <= 4 else "multiple_choice",
                    "question_content": raw["question_content"], "options": _ungraded_options(raw["options"]),
                    "answer": [], "explanation": "", "kp_ids": [], "media": [],
                    "source_ref": {"file": str(markdown_path.relative_to(source_root)), "number": raw["number"]},
                }
                if not question["question_content"]:
                    reason = "missing_stem"
                    rejected[reason] += 1
                else:
                    published.append(question)
            paper_id = _paper_id(exam_id, markdown_path)
            audit = {"template_id": paper_id, "source": str(markdown_path.relative_to(source_root)), "parsed": len(raw_questions), "published": len(published), "rejected": dict(rejected), "passed": bool(published)}
            audits.append(audit)
            if not audit["passed"]:
                if not published:
                    catalog["papers"].append({
                        "template_id": paper_id,
                        "exam_id": exam_id,
                        "year": re.search(r"(20\d{2})", markdown_path.parent.name).group(1) if re.search(r"(20\d{2})", markdown_path.parent.name) else "未标注年份",
                        "paper_type": "模拟题" if "模拟" in str(markdown_path) else "真题",
                        "title": f"{markdown_path.parent.name}（待整理）",
                        "question_count": 0,
                        "published": True,
                        "data_file": f"papers/{paper_id}.json",
                        "availability": "pending_structure_cleanup",
                    })
                    (papers_dir / f"{paper_id}.json").write_text(
                        json.dumps({"template_id": paper_id, "questions": []}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                continue
            title = markdown_path.parent.name
            year_match = re.search(r"(20\d{2})", title)
            catalog["papers"].append({
                "template_id": paper_id, "exam_id": exam_id, "year": year_match.group(1) if year_match else "未标注年份",
                "paper_type": "模拟题" if "模拟" in str(markdown_path) else "真题", "title": title,
                "question_count": len(published), "published": True, "data_file": f"papers/{paper_id}.json",
            })
            (papers_dir / f"{paper_id}.json").write_text(json.dumps({"template_id": paper_id, "questions": published}, ensure_ascii=False, indent=2), encoding="utf-8")
    assistant_json = source_root / "中医执业助理医师资格考试_2018-2023.json"
    if assistant_json.is_file():
        exam_name = "中医执业助理医师资格考试"
        exam_id = hashlib.sha1(exam_name.encode("utf-8")).hexdigest()[:12]
        catalog["exams"].append({"exam_id": exam_id, "name": exam_name})
        for raw_paper in json.loads(assistant_json.read_text(encoding="utf-8")):
            if not isinstance(raw_paper, dict):
                continue
            paper_id = f"{exam_id}-{raw_paper.get('year', 'unknown')}"
            published, rejected = [], defaultdict(int)
            for raw_index, raw in enumerate(raw_paper.get("items", []), start=1):
                options = [
                    {"option_id": str(option.get("label") or ""), "content": str(option.get("text") or "").strip()}
                    for option in raw.get("options", []) if isinstance(option, dict)
                ]
                answer = str(raw.get("answer") or "").strip()
                question = {
                    "question_id": f"{paper_id}-q{raw_index:04d}", "question_type": "single_choice",
                    "question_content": str(raw.get("stem") or "").strip(), "options": options,
                    "answer": [answer] if answer else [], "explanation": str(raw.get("analysis") or "").strip(),
                    "kp_ids": [], "media": [], "source_ref": {"file": assistant_json.name, "number": raw.get("id")},
                }
                reason = validate_question(question, require_answer=False)
                if reason:
                    rejected[reason] += 1
                else:
                    published.append(question)
            ids = [item["question_id"] for item in published]
            if len(ids) != len(set(ids)):
                rejected["duplicate_question_id"] += 1
            audit = {"template_id": paper_id, "source": assistant_json.name, "parsed": len(raw_paper.get("items", [])), "published": len(published), "rejected": dict(rejected), "passed": bool(published)}
            audits.append(audit)
            if not published:
                continue
            catalog["papers"].append({
                "template_id": paper_id, "exam_id": exam_id, "year": str(raw_paper.get("year") or "未标注年份"),
                "paper_type": "真题", "title": str(raw_paper.get("name") or paper_id), "question_count": len(published),
                "published": True, "data_file": f"papers/{paper_id}.json",
            })
            (papers_dir / f"{paper_id}.json").write_text(json.dumps({"template_id": paper_id, "questions": published}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"schema_version": "1.0", "papers": audits, "published_count": len(catalog["papers"])}
    (output_root / "audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification-source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--qualification-output", type=Path, required=True)
    parser.add_argument("--clinical-metadata", type=Path)
    parser.add_argument("--clinical-output", type=Path)
    args = parser.parse_args()
    build_qualification_papers(args.qualification_source, args.qualification_output, args.reference)
    if args.clinical_metadata and args.clinical_output:
        records = []
        for line in args.clinical_metadata.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload.get("original"), dict):
                records.append(payload["original"])
        cases, rejected = build_clinical_cases(records)
        args.clinical_output.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"published_cases": len(cases), "rejected_cases": rejected}, ensure_ascii=False))


if __name__ == "__main__":
    main()
