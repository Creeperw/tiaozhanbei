"""Build auditable qualification-paper and simulated-patient data assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable


QUESTION_START = re.compile(
    r"(?:^|\n|(?<=[。！？；]))\s*(\d{1,4})\s*[、．.]\s*"
    r"(?=[\s\S]{0,300}?(?:A|Ａ)\s*[、．.])"
)
NUMBERED_SECTION_START = re.compile(r"(?m)^\s*(?:#{1,3}\s*)?(\d{1,4})\s*[、．.]?\s*$")
OPTION_START = re.compile(r"(?<![A-Za-z0-9])([A-HＡ-Ｈ])\s*[、．.]\s*")
ANSWER_MARKER = re.compile(r"(?:#{1,3}\s*)?[【\[]?答案[】\]]?\s*[:：]?\s*", re.IGNORECASE)
EXPLANATION_MARKER = re.compile(r"(?:#{1,3}\s*)?[【\[]?解析[】\]]?\s*[:：]?\s*", re.IGNORECASE)
INLINE_ANSWER_SECTION = re.compile(r"(?im)^\s*#{1,3}\s*答案\s*解析\s*$")
INLINE_ANSWER_ENTRY = re.compile(
    r"(?:(?P<number_before>\d{1,4})\s*)?答案\s*[:：]\s*"
    r"(?P<answer>[A-HＡ-Ｈ](?:\s*[,，、/或]\s*[A-HＡ-Ｈ])*)\s*"
    r"(?:(?P<number_after>\d{1,4})\s*)?解析\s*[:：]\s*"
)
MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
TITLE_FORBIDDEN = re.compile(r"答案|解析|详解|讲解")


def _clean_visible_text(value: str) -> str:
    value = MARKDOWN_IMAGE.sub(" ", value or "")
    value = re.sub(r"(?m)^\s*#{1,6}\s*", "", value)
    return re.sub(r"\s+", " ", value).strip(" \n\t、，,；;")


def _answer_values(value: str) -> list[str]:
    match = re.search(r"[A-HＡ-Ｈ](?:\s*[,，、/或]\s*[A-HＡ-Ｈ])*", value or "")
    if not match:
        return []
    normalized = match.group(0).translate(str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH"))
    return re.findall(r"[A-H]", normalized)


def _split_answer_and_explanation(chunk: str) -> tuple[str, list[str], str]:
    answer_match = ANSWER_MARKER.search(chunk)
    explanation_match = EXPLANATION_MARKER.search(chunk)
    content_end = min(
        [match.start() for match in (answer_match, explanation_match) if match] or [len(chunk)]
    )
    visible = chunk[:content_end]
    answer_text = ""
    if answer_match:
        answer_end = explanation_match.start() if explanation_match and explanation_match.start() > answer_match.end() else len(chunk)
        answer_text = chunk[answer_match.end():answer_end]
    explanation = chunk[explanation_match.end():] if explanation_match else ""
    return visible, _answer_values(answer_text), _clean_visible_text(explanation)


def parse_inline_answer_section(markdown: str) -> tuple[str, dict[int, dict]]:
    section = INLINE_ANSWER_SECTION.search(markdown or "")
    if not section:
        return markdown, {}
    question_text = markdown[:section.start()]
    answer_text = markdown[section.end():]
    matches = list(INLINE_ANSWER_ENTRY.finditer(answer_text))
    answers: dict[int, dict] = {}
    last_number = 0
    for index, match in enumerate(matches):
        explicit_number = match.group("number_before") or match.group("number_after")
        number = int(explicit_number) if explicit_number else last_number + 1
        last_number = number
        explanation = answer_text[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(answer_text)
        ]
        answers[number] = {
            "answer": _answer_values(match.group("answer")),
            "explanation": _clean_visible_text(explanation),
        }
    return question_text, answers


def parse_markdown_questions(markdown: str) -> list[dict]:
    normalized_markdown = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    matches = list(QUESTION_START.finditer(normalized_markdown))
    questions: list[dict] = []
    for index, match in enumerate(matches):
        chunk = normalized_markdown[
            match.end():matches[index + 1].start() if index + 1 < len(matches) else len(normalized_markdown)
        ]
        visible, answer, explanation = _split_answer_and_explanation(chunk)
        option_matches = list(OPTION_START.finditer(visible))
        if option_matches:
            stem = visible[:option_matches[0].start()]
            options = [
                {
                    "option_id": option.group(1).translate(str.maketrans("ＡＢＣＤＥＦＧＨ", "ABCDEFGH")),
                    "content": _clean_visible_text(
                        visible[
                            option.end():option_matches[next_index + 1].start()
                            if next_index + 1 < len(option_matches)
                            else len(visible)
                        ]
                    ),
                }
                for next_index, option in enumerate(option_matches)
            ]
        else:
            stem, options = visible, []
        question = {
            "number": int(match.group(1)),
            "question_content": _clean_visible_text(stem),
            "options": [item for item in options if item["content"]],
        }
        if answer:
            question["answer"] = answer
        if explanation:
            question["explanation"] = explanation
        questions.append(question)
    return questions


def parse_answer_material(markdown: str) -> dict[int, dict]:
    normalized = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    section_matches = list(NUMBERED_SECTION_START.finditer(normalized))
    answers: dict[int, dict] = {}
    for index, match in enumerate(section_matches):
        chunk = normalized[match.end():section_matches[index + 1].start() if index + 1 < len(section_matches) else len(normalized)]
        _, answer, explanation = _split_answer_and_explanation(chunk)
        if answer or explanation:
            answers[int(match.group(1))] = {"answer": answer, "explanation": explanation}
    return answers


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
    if ANSWER_MARKER.search(content) or EXPLANATION_MARKER.search(content):
        return "answer_leak_in_stem"
    if MARKDOWN_IMAGE.search(content) or any(MARKDOWN_IMAGE.search(str(item.get("content") or "")) for item in question["options"]):
        return "unresolved_markdown_media"
    if "<table" in content.lower() or len(question["options"]) > 8:
        return "unparsed_compound_question"
    if any(ANSWER_MARKER.search(str(item.get("content") or "")) or EXPLANATION_MARKER.search(str(item.get("content") or "")) for item in question["options"]):
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
    return indexed


def _paper_id(exam_id: str, source: Path) -> str:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    return f"{exam_id}-{digest}"


def _paper_year(path: Path) -> str:
    match = re.search(r"(20\d{2})", str(path))
    return match.group(1) if match else "未标注年份"


def _set_number(path: Path) -> int | None:
    match = re.search(r"(\d{1,2})\s*$", path.parent.name)
    return int(match.group(1)) if match else None


def _paper_type(path: Path) -> str:
    source = str(path)
    if "模拟" in source:
        return "模拟题"
    if "题目还原" in source:
        return "回忆题"
    if "实践技能" in source:
        return "实践技能"
    return "真题"


def _pharmacist_subject(path: Path) -> str:
    source = str(path)
    if "中药一" in source or "专业知识（一）" in source or "专业知识一" in source:
        return "中药学专业知识（一）"
    if "中药二" in source or "专业知识（二）" in source or "专业知识二" in source:
        return "中药学专业知识（二）"
    if "中药综合" in source or "综合知识与技能" in source:
        return "中药学综合知识与技能"
    if "法规" in source or "药事管理" in source:
        return "药事管理与法规"
    return ""


def _paper_title(exam_name: str, path: Path, paper_type: str, year: str) -> str:
    set_number = _set_number(path)
    if paper_type == "模拟题":
        return f"模拟题（第 {set_number} 套）" if set_number else "模拟题"
    if paper_type == "回忆题":
        return f"回忆题（第 {set_number} 套）" if set_number else "回忆题"
    if paper_type == "实践技能":
        match = re.search(r"试卷[（(]([^）)]+)[）)]", path.parent.name)
        suffix = f"（{match.group(1)}）" if match else ""
        return f"{year} 年实践技能试卷{suffix}"
    subject = _pharmacist_subject(path) if "执业药师" in exam_name else ""
    if subject:
        return f"{year} 年 · {subject} · 真题"
    qualifier = "（一试）" if "一试" in str(path) else ""
    return f"{year} 年真题{qualifier}" if year != "未标注年份" else "真题"


def _catalog_sort_key(item: dict) -> tuple:
    title = str(item.get("title") or "")
    set_match = re.search(r"第\s*(\d+)\s*套", title)
    type_order = {"真题": 0, "实践技能": 1, "回忆题": 2, "模拟题": 3}
    return (
        str(item.get("exam_id") or ""),
        str(item.get("year") or "") == "未标注年份",
        str(item.get("year") or ""),
        type_order.get(str(item.get("paper_type") or ""), 9),
        int(set_match.group(1)) if set_match else 0,
        title,
    )


def _answer_group_key(path: Path) -> tuple[str, str]:
    return _paper_year(path), _pharmacist_subject(path)


def _question_from_raw(
    *,
    raw: dict,
    paper_id: str,
    source_root: Path,
    source_path: Path,
    answer_material: dict[int, dict],
    reference: dict[str, dict],
) -> dict:
    normalized_stem = re.sub(r"\s+", "", raw["question_content"])
    reference_item = reference.get(normalized_stem, {})
    supplemental = answer_material.get(raw["number"], {})
    answer = raw.get("answer") or supplemental.get("answer") or reference_item.get("answer") or []
    explanation = raw.get("explanation") or supplemental.get("explanation") or reference_item.get("explanation") or ""
    options = [
        {"option_id": str(option.get("option_id") or ""), "content": _clean_visible_text(str(option.get("content") or ""))}
        for option in raw.get("options", [])
        if _clean_visible_text(str(option.get("content") or ""))
    ]
    return {
        "question_id": f"{paper_id}-q{raw['number']:04d}",
        "question_type": "multiple_choice" if len(answer) > 1 else "single_choice",
        "question_content": _clean_visible_text(raw["question_content"]),
        "options": options,
        "answer": [str(value) for value in answer],
        "explanation": _clean_visible_text(str(explanation)),
        "kp_ids": [str(value) for value in reference_item.get("kp_ids", []) if str(value)],
        "media": [],
        "source_ref": {
            "file": str(source_path.relative_to(source_root)),
            "number": raw["number"],
            "answer_source": "same_document" if raw.get("answer") else "paired_material" if supplemental else "reference_bank" if reference_item else "",
        },
    }
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
    for old_paper in papers_dir.glob("*.json"):
        old_paper.unlink()
    catalog, audits = {"schema_version": "1.0", "exams": [], "papers": []}, []
    seen_exam_names: set[str] = set()

    for exam_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        exam_name = exam_dir.name
        exam_id = hashlib.sha1(exam_name.encode("utf-8")).hexdigest()[:12]
        catalog["exams"].append({"exam_id": exam_id, "name": exam_name})
        seen_exam_names.add(exam_name)
        markdown_paths = sorted(exam_dir.rglob("full.md"))
        answer_materials: dict[tuple[str, str], list[dict[int, dict]]] = defaultdict(list)
        question_paths: list[Path] = []

        for markdown_path in markdown_paths:
            markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
            question_markdown, inline_answers = parse_inline_answer_section(markdown)
            raw_trimmed = parse_markdown_questions(question_markdown)
            has_inline_answers = bool(inline_answers)
            has_question_content = bool(raw_trimmed)
            source_name = markdown_path.parent.name
            is_answer_material = (
                bool(TITLE_FORBIDDEN.search(source_name))
                and not has_question_content
                and not has_inline_answers
            )
            if is_answer_material:
                parsed_answers = parse_answer_material(markdown)
                if parsed_answers:
                    answer_materials[_answer_group_key(markdown_path)].append(parsed_answers)
                    audits.append({
                        "source": str(markdown_path.relative_to(source_root)),
                        "kind": "answer_material",
                        "parsed": len(parsed_answers),
                        "published": 0,
                        "rejected": {},
                        "passed": True,
                    })
                continue
            question_paths.append(markdown_path)

        for markdown_path in question_paths:
            markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
            question_markdown, inline_answers = parse_inline_answer_section(markdown)
            raw_questions = parse_markdown_questions(question_markdown)
            paired_answers: dict[int, dict] = dict(inline_answers)
            candidates = answer_materials.get(_answer_group_key(markdown_path), [])
            if not paired_answers and len(candidates) == 1:
                paired_answers = candidates[0]
            paper_id = _paper_id(exam_id, markdown_path)
            published, rejected = [], defaultdict(int)
            seen_numbers: set[int] = set()
            for raw in raw_questions:
                if raw["number"] in seen_numbers:
                    rejected["duplicate_question_number"] += 1
                    continue
                seen_numbers.add(raw["number"])
                question = _question_from_raw(
                    raw=raw,
                    paper_id=paper_id,
                    source_root=source_root,
                    source_path=markdown_path,
                    answer_material=paired_answers,
                    reference=reference,
                )
                reason = validate_question(question, require_answer=False)
                if reason:
                    rejected[reason] += 1
                    continue
                published.append(question)
            rejected_count = sum(rejected.values())
            passed = bool(published)
            audit = {
                "template_id": paper_id,
                "source": str(markdown_path.relative_to(source_root)),
                "kind": "question_paper",
                "parsed": len(raw_questions),
                "published": len(published),
                "rejected": dict(rejected),
                "ungraded": sum(not question["answer"] for question in published),
                "passed": passed,
            }
            audits.append(audit)
            if not passed:
                continue
            year = _paper_year(markdown_path)
            paper_type = _paper_type(markdown_path)
            title = _paper_title(exam_name, markdown_path, paper_type, year)
            catalog["papers"].append({
                "template_id": paper_id,
                "exam_id": exam_id,
                "year": year,
                "paper_type": paper_type,
                "title": title,
                "question_count": len(published),
                "published": True,
                "data_file": f"papers/{paper_id}.json",
            })
            (papers_dir / f"{paper_id}.json").write_text(
                json.dumps({"template_id": paper_id, "questions": published}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    assistant_json = source_root / "中医执业助理医师资格考试_2018-2023.json"
    if assistant_json.is_file():
        exam_name = "中医执业助理医师资格考试"
        exam_id = hashlib.sha1(exam_name.encode("utf-8")).hexdigest()[:12]
        if exam_name not in seen_exam_names:
            catalog["exams"].append({"exam_id": exam_id, "name": exam_name})
        for raw_paper in json.loads(assistant_json.read_text(encoding="utf-8")):
            if not isinstance(raw_paper, dict):
                continue
            year = str(raw_paper.get("year") or "未标注年份")
            paper_id = f"{exam_id}-{year}"
            published, rejected = [], defaultdict(int)
            for raw_index, raw in enumerate(raw_paper.get("items", []), start=1):
                options = [
                    {"option_id": str(option.get("label") or ""), "content": _clean_visible_text(str(option.get("text") or ""))}
                    for option in raw.get("options", []) if isinstance(option, dict)
                ]
                answer = str(raw.get("answer") or "").strip()
                question = {
                    "question_id": f"{paper_id}-q{raw_index:04d}",
                    "question_type": "single_choice",
                    "question_content": _clean_visible_text(str(raw.get("stem") or "")),
                    "options": options,
                    "answer": [answer] if answer else [],
                    "explanation": _clean_visible_text(str(raw.get("analysis") or "")),
                    "kp_ids": [],
                    "media": [],
                    "source_ref": {"file": assistant_json.name, "number": raw.get("id")},
                }
                reason = validate_question(question, require_answer=False)
                if reason:
                    rejected[reason] += 1
                else:
                    published.append(question)
            audits.append({
                "template_id": paper_id,
                "source": assistant_json.name,
                "kind": "question_paper",
                "parsed": len(raw_paper.get("items", [])),
                "published": len(published),
                "rejected": dict(rejected),
                "ungraded": sum(not question["answer"] for question in published),
                "passed": bool(published),
            })
            if not published:
                continue
            catalog["papers"].append({
                "template_id": paper_id,
                "exam_id": exam_id,
                "year": year,
                "paper_type": "真题",
                "title": f"{year} 年真题",
                "question_count": len(published),
                "published": True,
                "data_file": f"papers/{paper_id}.json",
            })
            (papers_dir / f"{paper_id}.json").write_text(
                json.dumps({"template_id": paper_id, "questions": published}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    catalog["exams"] = sorted(catalog["exams"], key=lambda item: item["name"])
    catalog["papers"] = sorted(catalog["papers"], key=_catalog_sort_key)
    (output_root / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "schema_version": "1.0",
        "papers": audits,
        "published_count": len(catalog["papers"]),
        "published_question_count": sum(paper["question_count"] for paper in catalog["papers"]),
        "quality_gate": {
            "answer_leak_in_option": sum(audit.get("rejected", {}).get("answer_leak_in_option", 0) for audit in audits),
            "unparsed_compound_question": sum(audit.get("rejected", {}).get("unparsed_compound_question", 0) for audit in audits),
        },
    }
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
