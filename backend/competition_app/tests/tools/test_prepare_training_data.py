from competition_app.tools.prepare_training_data import (
    build_qualification_papers,
    build_clinical_cases,
    parse_markdown_questions,
    validate_question,
)


def test_parse_markdown_questions_splits_compact_stem_and_options() -> None:
    questions = parse_markdown_questions(
        """# 2024 年测试卷

1、下列说法正确的是A、甲B、乙C、丙D、丁

2、第二题
A、甲
B、乙
"""
    )

    assert questions == [
        {
            "number": 1,
            "question_content": "下列说法正确的是",
            "options": [
                {"option_id": "A", "content": "甲"},
                {"option_id": "B", "content": "乙"},
                {"option_id": "C", "content": "丙"},
                {"option_id": "D", "content": "丁"},
            ],
        },
        {
            "number": 2,
            "question_content": "第二题",
            "options": [{"option_id": "A", "content": "甲"}, {"option_id": "B", "content": "乙"}],
        },
    ]


def test_validate_question_rejects_missing_answer_and_accepts_reference_enriched_question() -> None:
    question = {
        "question_id": "q1",
        "question_type": "single_choice",
        "question_content": "题干",
        "options": [{"option_id": "A", "content": "甲"}],
        "answer": [],
        "explanation": "",
        "kp_ids": [],
        "media": [],
        "source_ref": {},
    }

    assert validate_question(question) == "missing_answer"
    question["answer"] = ["A"]
    assert validate_question(question) == ""


def test_markdown_paper_is_published_with_empty_answers_when_its_structure_is_valid(tmp_path) -> None:
    source = tmp_path / "综合套题" / "中医执业医师资格考试" / "2024 年真题"
    source.mkdir(parents=True)
    (source / "full.md").write_text("1、题干\nA、甲\nB、乙", encoding="utf-8")
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    report = build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    paper = report["papers"][0]
    payload = (tmp_path / "out" / "papers" / f"{paper['template_id']}.json").read_text(encoding="utf-8")

    assert paper["passed"] is True
    assert '"answer": []' in payload


def test_markdown_parser_separates_answers_explanations_and_embedded_next_question() -> None:
    questions = parse_markdown_questions(
        """13. “中正之官”指的是
A. 胆
B. 小肠
C. 胃
D. 大肠
E. 膀胱
答案: A
解析：《素问》说：“胆者，中正之官，决断出焉。14.下列叙述错误的是A.心合脉B.肝合爪C.脾合肉D.肺合皮E.肾合骨
答案：B
解析：肝合筋。故 B 错误。"""
    )

    assert [question["number"] for question in questions] == [13, 14]
    assert [option["option_id"] for option in questions[0]["options"]] == ["A", "B", "C", "D", "E"]
    assert questions[0]["answer"] == ["A"]
    assert questions[0]["explanation"] == "《素问》说：“胆者，中正之官，决断出焉。"
    assert questions[1]["question_content"] == "下列叙述错误的是"
    assert questions[1]["answer"] == ["B"]
    assert questions[1]["explanation"] == "肝合筋。故 B 错误。"


def test_source_named_with_parse_words_is_published_when_it_contains_questions(tmp_path) -> None:
    source = tmp_path / "综合套题" / "执业药师职业资格考试（中药学类）" / "中药一真题（15-24年）" / "2024 中药一真题解析"
    source.mkdir(parents=True)
    (source / "full.md").write_text("1、题干A、甲B、乙C、丙D、丁\n答案：C\n解析：说明。", encoding="utf-8")
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    catalog = __import__("json").loads((tmp_path / "out" / "catalog.json").read_text(encoding="utf-8"))
    payload = __import__("json").loads((tmp_path / "out" / catalog["papers"][0]["data_file"]).read_text(encoding="utf-8"))

    assert catalog["papers"][0]["title"] == "2024 年 · 中药学专业知识（一） · 真题"
    assert payload["questions"][0]["answer"] == ["C"]


def test_incomplete_paper_is_not_published(tmp_path) -> None:
    source = tmp_path / "综合套题" / "中西医结合执业医师资格考试" / "执业模拟题" / "执业模拟1"
    source.mkdir(parents=True)
    (source / "full.md").write_text(
        "1、题干A、甲B、乙C、丙D、丁\n1、重复题号A、甲B、乙C、丙D、丁\n2、题干A、甲B、乙C、丙D、丁",
        encoding="utf-8",
    )
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    report = build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)

    assert report["published_count"] == 0
    assert report["papers"][0]["passed"] is False
    assert report["papers"][0]["rejected"]["duplicate_question_number"] == 1


def test_answer_material_is_merged_without_becoming_a_separate_paper(tmp_path) -> None:
    source_root = tmp_path / "综合套题"
    question_source = source_root / "中医执业医师资格考试" / "考题2018-精选"
    answer_source = source_root / "中医执业医师资格考试" / "考题2018年答案-解析"
    question_source.mkdir(parents=True)
    answer_source.mkdir(parents=True)
    (question_source / "full.md").write_text("1.五行属火的是\nA.木\nB.金\nC.火\nD.水", encoding="utf-8")
    (answer_source / "full.md").write_text(
        "1.\n## 【答案】C\n【解析】火曰炎上。", encoding="utf-8"
    )
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    build_qualification_papers(source_root, tmp_path / "out", reference)
    catalog = __import__("json").loads((tmp_path / "out" / "catalog.json").read_text(encoding="utf-8"))
    paper = catalog["papers"][0]
    payload = __import__("json").loads((tmp_path / "out" / paper["data_file"]).read_text(encoding="utf-8"))

    assert len(catalog["papers"]) == 1
    assert paper["title"] == "2018 年真题"
    assert "答案" not in paper["title"]
    assert "解析" not in paper["title"]
    assert payload["questions"][0]["answer"] == ["C"]
    assert payload["questions"][0]["explanation"] == "火曰炎上。"


def test_catalog_orders_numbered_sets_numerically(tmp_path) -> None:
    source_root = tmp_path / "综合套题" / "中西医结合执业助理医师资格考试" / "助理模拟题"
    for number in (1, 10, 2):
        source = source_root / f"助理模拟{number}"
        source.mkdir(parents=True)
        (source / "full.md").write_text("1、题干A、甲B、乙C、丙D、丁", encoding="utf-8")
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    catalog = __import__("json").loads((tmp_path / "out" / "catalog.json").read_text(encoding="utf-8"))

    assert [paper["title"] for paper in catalog["papers"]] == ["模拟题（第 1 套）", "模拟题（第 2 套）", "模拟题（第 10 套）"]


def test_inline_answer_section_is_merged_into_the_question_paper(tmp_path) -> None:
    source = tmp_path / "综合套题" / "执业药师职业资格考试（中药学类）" / "中药一真题（15-24年）" / "2015 年真题及解析"
    source.mkdir(parents=True)
    (source / "full.md").write_text(
        """1、第一题A、甲B、乙C、丙D、丁
2、第二题A、甲B、乙C、丙D、丁

## 答案解析

答案：C1解析：第一题解析。
2 答案：B解析：第二题解析。""",
        encoding="utf-8",
    )
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    catalog = __import__("json").loads((tmp_path / "out" / "catalog.json").read_text(encoding="utf-8"))
    payload = __import__("json").loads((tmp_path / "out" / catalog["papers"][0]["data_file"]).read_text(encoding="utf-8"))

    assert payload["questions"][0]["answer"] == ["C"]
    assert payload["questions"][0]["explanation"] == "第一题解析。"
    assert payload["questions"][1]["answer"] == ["B"]
    assert payload["questions"][1]["explanation"] == "第二题解析。"


def test_build_rejects_answer_leaks_and_keeps_only_clean_options(tmp_path) -> None:
    source = tmp_path / "综合套题" / "中西医结合执业助理医师资格考试" / "助理模拟题" / "助理模拟1"
    source.mkdir(parents=True)
    (source / "full.md").write_text(
        "1.题干\nA.甲\nB.乙\nC.丙\nD.丁\nE.戊\n答案：C\n解析：说明。",
        encoding="utf-8",
    )
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    catalog = __import__("json").loads((tmp_path / "out" / "catalog.json").read_text(encoding="utf-8"))
    payload = __import__("json").loads((tmp_path / "out" / catalog["papers"][0]["data_file"]).read_text(encoding="utf-8"))
    question = payload["questions"][0]

    assert catalog["papers"][0]["title"] == "模拟题（第 1 套）"
    assert [option["option_id"] for option in question["options"]] == ["A", "B", "C", "D", "E"]
    assert all("答案" not in option["content"] and "解析" not in option["content"] for option in question["options"])
    assert question["answer"] == ["C"]
    assert question["explanation"] == "说明。"


def test_build_clinical_cases_groups_questions_and_keeps_diagnosis_separate_from_treatment() -> None:
    records = [
        {
            "题目大来源": "CMB-Clin", "题目id": "q-diagnosis", "题目章节来源": "案例分析-腹外疝",
            "题型": "临床案例问答", "题目内容": "病人，男，49岁。主诉：腹痛。问题：简述诊断及诊断依据。",
            "题目答案": "诊断：嵌顿性腹股沟斜疝。诊断依据：腹痛。",
            "source": {"case_id": "case-1"}, "kp_ids": ["kp1"], "标签": ["外科"],
        },
        {
            "题目大来源": "CMB-Clin", "题目id": "q-treatment", "题目章节来源": "案例分析-腹外疝",
            "题型": "临床案例问答", "题目内容": "病人，男，49岁。问题：简述治疗原则。",
            "题目答案": "应紧急手术治疗。", "source": {"case_id": "case-1"}, "kp_ids": ["kp2"], "标签": ["外科"],
        },
    ]

    cases, rejected = build_clinical_cases(records)

    assert rejected == []
    assert cases[0]["题目id"] == "case-1"
    assert cases[0]["题目答案"] == "嵌顿性腹股沟斜疝"
    assert cases[0]["补充资料"]["treatment"] == "应紧急手术治疗。"
