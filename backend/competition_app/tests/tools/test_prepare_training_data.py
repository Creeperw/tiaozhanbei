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


def test_markdown_paper_with_repeated_option_letters_is_still_published_as_ungraded_content(tmp_path) -> None:
    source = tmp_path / "综合套题" / "中西医结合执业助理医师资格考试" / "模拟卷"
    source.mkdir(parents=True)
    (source / "full.md").write_text("1、第一题A、甲B、乙2、第二题A、丙B、丁", encoding="utf-8")
    reference = tmp_path / "参考.json"
    reference.write_text("[]", encoding="utf-8")

    report = build_qualification_papers(tmp_path / "综合套题", tmp_path / "out", reference)
    paper = report["papers"][0]
    payload = __import__("json").loads((tmp_path / "out" / "papers" / f"{paper['template_id']}.json").read_text(encoding="utf-8"))

    assert paper["passed"] is True
    assert payload["questions"][0]["answer"] == []
    assert len({option["option_id"] for option in payload["questions"][0]["options"]}) == len(payload["questions"][0]["options"])


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
