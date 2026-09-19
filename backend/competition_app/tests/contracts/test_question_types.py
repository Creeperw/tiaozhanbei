"""题型词表：英文枚举、中文同义、未识别写法的处理。

词表是组卷链路上「这道题属于哪种题型」的唯一判据，此前每个环节各写一张
别名表，覆盖范围互不一致，线上已经因此出过两次事故（网络题写成英文枚举被
整批丢弃；卷面题型标注与配额不同源导致整卷返修）。
"""

from __future__ import annotations

from competition_app.contracts.question_types import (
    KNOWN_QUESTION_TYPES,
    is_known_question_type,
    normalize_question_type,
)


def test_english_platform_enums_are_recognised() -> None:
    """平台英文枚举必须归一成中文规范名。

    网络题抽取器的提示词只声明字段名、不声明允许值，模型会把平台枚举原样
    写回来。候选准入的题型过滤只认中文名，这些题此前被判成题型不一致整批
    丢弃——线上实测 8 道已入库的网络题一道都进不了候选池。
    """

    assert normalize_question_type("single_choice") == "单项选择题"
    assert normalize_question_type("multiple_choice") == "多项选择题"
    assert normalize_question_type("true_false") == "判断题"
    assert normalize_question_type("judge") == "判断题"
    assert normalize_question_type("fill_blank") == "填空题"
    assert normalize_question_type("short_answer") == "简答题"
    # 案例题在题库里按简答题计配额、按简答题写卷面，英文写法也要归到同一处。
    assert normalize_question_type("case_quiz") == "简答题"
    assert normalize_question_type("case_analysis") == "简答题"


def test_alias_lookup_ignores_case_spaces_and_underscores() -> None:
    """大小写、空格、下划线不改变题型。"""

    for value in (
        "single_choice",
        "Single_Choice",
        "singlechoice",
        "single choice",
        "  SINGLE_CHOICE  ",
        "SINGLE CHOICE",
    ):
        assert normalize_question_type(value) == "单项选择题", value


def test_chinese_synonyms_are_normalised() -> None:
    """中文同义写法归一，避免同一知识点下同时存在多种写法。"""

    assert normalize_question_type("单选题") == "单项选择题"
    assert normalize_question_type("单项选择") == "单项选择题"
    assert normalize_question_type("多选题") == "多项选择题"
    assert normalize_question_type("多项选择") == "多项选择题"
    assert normalize_question_type("简答") == "简答题"
    assert normalize_question_type("问答") == "简答题"
    assert normalize_question_type("问答题") == "简答题"
    assert normalize_question_type("临床案例问答") == "简答题"
    assert normalize_question_type("病例分析/实践技能") == "简答题"
    assert normalize_question_type("案例分析题") == "简答题"


def test_unknown_types_pass_through_so_gaps_stay_visible() -> None:
    """词表不认识的值原样返回。

    兜底成某个默认题型会让未知写法混进卷面，也会让词表缺口永远不被发现：
    入库侧要按「未映射」计数上报，靠的就是这里的原样透传。
    """

    for value in ("论述题", "名词解释", "病案分析题", "分析题", "未分类"):
        assert normalize_question_type(value) == value
        assert normalize_question_type(value) not in KNOWN_QUESTION_TYPES
        assert is_known_question_type(value) is False


def test_empty_values_normalise_to_empty_string() -> None:
    assert normalize_question_type("") == ""
    assert normalize_question_type("   ") == ""
    assert normalize_question_type(None) == ""


def test_canonical_names_are_idempotent() -> None:
    """规范名再归一仍是自己，避免二次归一改写出新写法。"""

    for value in KNOWN_QUESTION_TYPES:
        assert normalize_question_type(value) == value
        assert is_known_question_type(value) is True
