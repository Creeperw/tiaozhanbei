"""resolve_topic 的判别力回归：特异性（逆文档频率）与片段覆盖率。

线上实测的失败形态：专家智能体给出的 75 字蓝图范围整句里含“治疗原则”，
而“治疗原则”同时是 33 个不同学科的知识点名，于是 40 个知识点并列 0.9500
并挤占前 5 名；同时“鉴别”“治疗”“阳病”这类 2 字碎片只在全库出现一次
（逆文档频率无法降权），靠长整句的偶然子串命中就拿 0.92。

两个因子都只由知识点库自身的分布与查询的结构切分决定，不依赖任何学科的
措辞，因此不是关键词表。这里的断言全部落在“排序与区分度”上，不锁死具体
分数值。
"""

from pathlib import Path

import pytest

from competition_app.tests.tools.test_knowledge_delivery import build_backend


def knowledge_point(
    kp_id: str,
    *,
    lv1: str,
    lv3: str,
    lv2: str = "节点",
    other_name: str = "",
) -> dict:
    return {
        kp_id: {
            "kp_id": kp_id,
            "kp_lv1": lv1,
            "kp_lv2": lv2,
            "kp_lv3": lv3,
            "other_name": other_name,
            "order": "1",
            "raw_content": [],
            "exam_bridges": [],
        }
    }


def resolve(backend, kps: dict, query: str, limit: int = 10) -> list[dict]:
    """在 fixture 知识点之上追加条目后解析主题。

    ``ensure_hierarchy`` 会按文件重建 ``kps``，所以必须先把层次读出来再追加，
    否则追加的条目会被覆盖掉。
    """
    backend.map.ensure_hierarchy()
    backend.map.kps.update(kps)
    return backend.map.resolve_topic(query, limit=limit)


def scores_by_id(matches: list[dict]) -> dict[str, float]:
    return {item["kp_id"]: item["score"] for item in matches}


def repeated_catalog_points(name: str, count: int) -> dict:
    """构造跨学科同名的目录式字段。

    线上实测“治疗原则”同时是 33 个不同学科的知识点名，规模本身就是判别
    信号的来源，所以 fixture 要如实重现这个规模，不能用三两条代替。
    """
    points: dict = {}
    for index in range(count):
        points.update(
            knowledge_point(
                f"KP_REPEAT_{index:02d}",
                lv1=f"学科{index:02d}",
                lv3=name,
            )
        )
    return points


def test_two_character_fragment_in_a_long_segment_cannot_tie_the_topic(
    tmp_path: Path,
) -> None:
    """2 字碎片只占所在主题片段的一小部分，不能与覆盖整段的字段同分。

    “鉴别”在全库只出现一次，逆文档频率给它满权重；改前它靠长片段里的偶然
    子串拿到 0.92，与真正对口的“太阳病提纲”只差 0.03。
    """
    backend = build_backend(tmp_path)
    matches = resolve(
        backend,
        {
            **knowledge_point("KP_TOPIC", lv1="伤寒论选读", lv3="太阳病提纲"),
            **knowledge_point("KP_FRAGMENT", lv1="中药药剂学", lv3="鉴别"),
        },
        "太阳病提纲证 中风伤寒温病分类鉴别",
    )
    scores = scores_by_id(matches)

    assert "KP_TOPIC" in scores and "KP_FRAGMENT" in scores
    assert scores["KP_TOPIC"] > scores["KP_FRAGMENT"] * 2


def test_repeated_catalog_field_cannot_tie_the_named_topic(tmp_path: Path) -> None:
    """跨学科同名的目录式字段（“治疗原则”）不是判别特征。

    线上实测它同时是 33 个学科的知识点名，长查询一旦含这四个字就把它们全部
    拉成同分。特异性权重只由库内频次决定，不依赖学科措辞。
    """
    backend = build_backend(tmp_path)
    matches = resolve(
        backend,
        {
            **knowledge_point("KP_UNIQUE", lv1="金匮要略", lv3="表里先后治则"),
            **repeated_catalog_points("治疗原则", 33),
        },
        "表里先后治疗原则",
    )
    scores = scores_by_id(matches)

    assert matches[0]["kp_id"] == "KP_UNIQUE"
    repeated = [score for kp_id, score in scores.items() if kp_id.startswith("KP_REPEAT_")]
    assert repeated
    assert all(score < scores["KP_UNIQUE"] / 2 for score in repeated)


def test_long_instruction_sentence_does_not_produce_a_tie_block(tmp_path: Path) -> None:
    """真实失败输入：75 字蓝图范围整句不得让跨学科同名知识点挤占前 5 名。"""
    backend = build_backend(tmp_path)
    matches = resolve(
        backend,
        {
            **knowledge_point("KP_TOPIC", lv1="伤寒论选读", lv3="太阳病提纲"),
            **repeated_catalog_points("治疗原则", 33),
        },
        "检索太阳病篇总纲类简答题，主题限定为太阳病提纲证、中风伤寒温病分类鉴别、"
        "太阳病传变、合病并病及表里先后治疗原则，正式题库优先，不足时按全局补题顺序补足",
    )
    scores = scores_by_id(matches)

    assert matches[0]["kp_id"] == "KP_TOPIC"
    repeated = [score for kp_id, score in scores.items() if kp_id.startswith("KP_REPEAT_")]
    assert repeated
    assert all(score < scores["KP_TOPIC"] * 0.6 for score in repeated)


def test_query_segment_that_is_a_prefix_of_the_knowledge_point_still_resolves(
    tmp_path: Path,
) -> None:
    """查询片段是知识点名的前缀时仍须召回（“表里先后” -> “表里先后治则”）。

    改前这种查询片段既不是字段的子串、子序列比例也不足 0.8，会完全漏召回。
    查询带上前缀片段是为了让整串不等于任何知识点全文，否则整串命中会先把
    这条知识点捞回来，掩盖掉要验证的分支。
    """
    backend = build_backend(tmp_path)
    matches = resolve(
        backend,
        knowledge_point("KP_PREFIX", lv1="金匮要略", lv3="表里先后治则"),
        "太阳病提纲证 表里先后",
    )

    assert matches
    assert matches[0]["kp_id"] == "KP_PREFIX"
    assert matches[0]["score"] > 0.0


def test_two_character_segment_does_not_expand_to_a_homonym(tmp_path: Path) -> None:
    """2 字片段不得反向扩展：它会把同名异义的知识点拉进候选。

    线上实测 kp_query 里的“中风”“伤寒”把金匮要略的内科“中风病”“心中风”和
    温病学的“肠伤寒”拉进前 5，并排在伤寒论的“太阳中风证”之前。这些知识点
    只覆盖查询里的一个字面片段，并不覆盖其余片段，两个字的名词歧义过大。
    """
    backend = build_backend(tmp_path)
    matches = resolve(
        backend,
        {
            **knowledge_point("KP_SUN_STROKE", lv1="伤寒论选读", lv3="太阳中风证"),
            **knowledge_point("KP_APOPLEXY", lv1="金匮要略", lv3="中风病"),
        },
        "太阳病提纲证 中风",
    )

    assert "KP_APOPLEXY" not in scores_by_id(matches)


def test_short_query_still_scores_the_named_knowledge_point_at_full_marks(
    tmp_path: Path,
) -> None:
    """回归：常规短查询里被点名的知识点仍是满分，不受两个判别因子影响。"""
    backend = build_backend(tmp_path)

    matches = resolve(backend, {}, "四君子汤 组成 功效")

    assert matches[0]["kp_id"] == "KP_1"
    assert matches[0]["score"] == pytest.approx(1.0)
