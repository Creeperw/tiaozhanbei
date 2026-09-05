from __future__ import annotations

"""Build the frozen, construct-valid D1 paired A/B dataset.

The legacy AB100 file only froze learner prompts.  This V2 fixture also freezes
the atomic claim, both evidence texts, the gold semantic relation and the
expected rule scope.  It is evaluation-only and never writes business state.
"""

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
LEGACY_DATASET = ROOT / "datasets" / "semantic_conflict_closure_ab100_20260826.jsonl"
DATASET = ROOT / "datasets" / "semantic_conflict_closure_ab100_v2_20260901.jsonl"
CSV_DATASET = DATASET.with_suffix(".csv")
REVIEW_DATASET = DATASET.with_name(f"{DATASET.stem}.human_review.csv")
MANIFEST = DATASET.with_suffix(".manifest.json")
RULE_ID = "semantic_conflict_pair_closure_v1"
FIXTURE_VERSION = "d1-frozen-evidence-2.0"


# Each tuple is (topic, authoritative atomic claim, contradictory atomic claim,
# relation dimension).  The second statement explicitly addresses the same
# predicate; it is a synthetic corrupted reference, not a medical assertion.
TARGET_CLAIMS: list[tuple[str, str, str, str]] = [
    ("阴阳互根互用", "阴阳互根强调双方相互依存，并以对方的存在作为自身存在的条件。", "阴阳互根表示双方彼此完全独立，任何一方都不以对方的存在为条件。", "definition_negation"),
    ("五行生克制化", "五行相生体现促进资生，相克体现制约约束，二者共同维持动态协调。", "五行相生与相克含义完全相同，都只表示单向促进，不包含制约关系。", "relation_reversal"),
    ("心藏象", "心的主要生理功能包括主血脉，并与神志活动密切相关。", "心不主血脉，也与神志活动没有关系。", "function_negation"),
    ("肺主宣发肃降", "肺的宣发偏向向上向外布散，肃降偏向向下向内清肃通降。", "肺的宣发只向下向内运行，肃降只向上向外布散。", "direction_swap"),
    ("脾主运化", "脾主运化包括运化水谷和运化水液两个相互联系的方面。", "脾主运化只包括运化水谷，完全不涉及水液的运化。", "scope_exclusion"),
    ("肝主疏泄", "肝主疏泄的重要作用之一是调畅全身气机。", "肝主疏泄与气机调畅无关，其作用仅限于储藏精气。", "function_negation"),
    ("肾藏精", "肾藏精包括贮藏先天之精和后天之精，并与生长发育生殖相关。", "肾不贮藏先天之精或后天之精，也不参与生长发育生殖。", "scope_negation"),
    ("气血津液关系", "气、血、津液在生成、运行和功能上相互联系、相互影响。", "气、血、津液彼此完全独立，生成和运行之间不存在相互影响。", "relation_negation"),
    ("经络总论", "经络具有运行气血、联络脏腑形体官窍和沟通内外的作用。", "经络既不运行气血，也不联络脏腑形体官窍。", "function_negation"),
    ("八纲辨证", "八纲由阴阳、表里、寒热、虚实四对纲领组成。", "八纲只包括表里、寒热和虚实，不包括阴阳。", "component_exclusion"),
    ("气血辨证", "气虚侧重功能不足，气滞侧重气机不畅，血虚侧重濡养不足，血瘀侧重血行不畅。", "气虚、气滞、血虚和血瘀在病机上没有区别，都只表示血行不畅。", "category_collapse"),
    ("脏腑辨证", "脏腑辨证依据脏腑生理功能和病理表现辨别病位与证候。", "脏腑辨证不考虑脏腑功能或病位，只按照症状出现的先后排序。", "criterion_reversal"),
    ("六经辨证", "六经辨证以太阳、阳明、少阳、太阴、少阴、厥阴为基本辨证层次。", "六经辨证只有卫分、气分、营分、血分四个层次。", "framework_swap"),
    ("卫气营血辨证", "卫气营血辨证通常按卫分、气分、营分、血分分析温热病的浅深层次。", "卫气营血辨证以太阳、阳明、少阳、太阴、少阴、厥阴作为分层。", "framework_swap"),
    ("三焦辨证", "三焦辨证按上焦、中焦、下焦分析湿热病邪所在层次及传变。", "三焦辨证只区分表证和里证，不使用上焦、中焦、下焦层次。", "framework_negation"),
    ("心气虚与心阳虚", "心阳虚一般在心气虚表现基础上兼见畏寒肢冷等虚寒征象。", "心阳虚与心气虚完全相同，不会出现任何虚寒征象。", "feature_exclusion"),
    ("肝气郁结证", "肝气郁结的核心病机是肝失疏泄、气机郁滞。", "肝气郁结的核心病机是肝血充盛、气机过度通畅。", "mechanism_reversal"),
    ("脾气虚证", "脾气虚常以食少、腹胀、便溏、倦怠乏力等运化不足表现为辨证依据。", "脾气虚不会影响饮食和大便，核心表现只有高热和口渴。", "feature_reversal"),
    ("肾阴虚与肾阳虚", "肾阴虚偏虚热，肾阳虚偏虚寒，二者寒热倾向不同。", "肾阴虚偏虚寒而肾阳虚偏虚热，二者寒热属性应当完全对调。", "attribute_swap"),
    ("痰湿证", "痰湿证的病机与津液输布失常、痰湿停聚有关。", "痰湿证与津液代谢无关，病机只是血液耗损。", "mechanism_negation"),
    ("血瘀证", "血瘀证以血行不畅或瘀血内阻为核心病机。", "血瘀证表示血液运行异常通畅，不存在瘀阻。", "mechanism_reversal"),
    ("风寒感冒", "风寒感冒通常以恶寒较重、发热较轻、无汗或少汗等为常见辨析点。", "风寒感冒的典型辨析点是高热重、恶寒轻并以明显汗出为必备条件。", "feature_reversal"),
    ("风热感冒", "风热感冒通常以发热较著、恶风、咽喉不适等偏热表现为辨析点。", "风热感冒不出现任何偏热表现，其必备特征是恶寒重而完全不发热。", "feature_reversal"),
    ("四君子汤", "四君子汤由人参、白术、茯苓、炙甘草组成，基本功用是益气健脾。", "四君子汤由熟地黄、当归、白芍、川芎组成，基本功用是补血调血。", "composition_swap"),
    ("四物汤", "四物汤由熟地黄、当归、白芍、川芎组成，是补血调血的基础方。", "四物汤由人参、白术、茯苓、炙甘草组成，是益气健脾的基础方。", "composition_swap"),
    ("逍遥散", "逍遥散的方义围绕疏肝解郁、养血健脾展开。", "逍遥散的方义只在峻下热结，与疏肝、养血和健脾均无关。", "function_reversal"),
    ("六味地黄丸", "六味地黄丸的基本功用是滋补肾阴。", "六味地黄丸的基本功用是峻补肾阳，完全不用于肾阴不足。", "function_reversal"),
    ("麻黄汤与桂枝汤", "麻黄汤偏治外感风寒表实无汗，桂枝汤偏治外感风寒表虚有汗。", "麻黄汤与桂枝汤的有汗无汗适用特点完全相反：麻黄汤只治有汗，桂枝汤只治无汗。", "indication_swap"),
    ("原穴", "原穴是脏腑原气输注、经过和留止于十二经脉四肢部位的腧穴。", "原穴与脏腑原气和十二经脉均无关系，只指头面部的任意穴位。", "definition_negation"),
    ("五输穴", "五输穴按井、荥、输、经、合的次序分布于十二经四肢肘膝以下。", "五输穴只有原、络、郄、募四类，并不包括井、荥、输、经、合。", "component_swap"),
    ("中医学整体观念", "中医学整体观念强调人体自身整体性以及人与自然、社会环境的联系。", "中医学整体观念认为人体各部分彼此孤立，人与自然环境也不存在联系。", "definition_negation"),
    ("阴阳对立制约", "阴阳对立制约指属性相反的双方相互斗争、相互制约。", "阴阳对立制约表示双方只会相互促进，不存在对立或制约。", "relation_reversal"),
    ("阴阳消长转化", "阴阳消长是量的增减变化，阴阳转化是在一定条件下向对立面的质变。", "阴阳消长与阴阳转化完全相同，都只指位置移动，不涉及量变或质变。", "concept_collapse"),
    ("五行相乘相侮", "五行相乘是相克太过，相侮是反向克制，二者方向不同。", "五行相乘和相侮方向完全相同，均表示正常相生。", "direction_reversal"),
    ("精气学说", "精气学说认为精气是构成天地万物的本原，并处于运动变化之中。", "精气学说认为精气不能构成任何事物，并且永远静止不变。", "definition_negation"),
    ("神的概念", "广义之神概括人体生命活动，狭义之神主要指意识、思维、情志等精神活动。", "广义之神只指意识思维，狭义之神反而概括全部生命活动。", "scope_swap"),
    ("心主血脉", "心主血脉是指心气推动和调控血液在脉中运行。", "心主血脉表示心与血液运行无关，血液只在经外静止。", "function_negation"),
    ("肺主气司呼吸", "肺主气、司呼吸包括主持呼吸运动并参与一身之气的生成和调节。", "肺只参与饮食消化，不参与呼吸或一身之气的调节。", "function_negation"),
    ("脾主升清", "脾主升清指脾气将水谷精微等向上输布，并维持内脏位置相对稳定。", "脾主升清表示脾气只把浊物向下排出，与精微上输和内脏位置无关。", "direction_reversal"),
    ("肝藏血", "肝藏血具有贮藏血液和调节血量的作用。", "肝藏血既不贮藏血液，也不能调节机体各部分的血量。", "function_negation"),
    ("肾主水", "肾主水通过肾气气化参与全身水液代谢的调节。", "肾主水与水液代谢无关，只表示肾储存食物。", "function_negation"),
    ("胆主决断", "胆主决断与人的判断、作出决定和应对事物的能力相关。", "胆主决断与判断或决定无关，只表示胆直接生成血液。", "function_negation"),
    ("胃主受纳腐熟", "胃主受纳是接受容纳饮食，腐熟水谷是对饮食进行初步消化。", "胃主受纳指排出饮食，腐熟水谷与消化过程完全无关。", "definition_reversal"),
    ("气机升降出入", "升、降、出、入是气运动的基本形式。", "气的运动只有静止一种状态，不存在升、降、出、入。", "component_negation"),
    ("津液输布", "津液代谢包括生成、输布和排泄等相互衔接的环节。", "津液只生成而不输布也不排泄，各环节彼此无关。", "process_negation"),
    ("十二经脉流注", "十二经脉气血流注首尾相贯，构成循环衔接的次序。", "十二经脉彼此没有衔接关系，也不存在循环流注次序。", "relation_negation"),
    ("奇经八脉", "奇经八脉具有统率、联络和调节十二经脉气血等作用。", "奇经八脉与十二经脉完全隔绝，不具有联络或调节作用。", "function_negation"),
    ("舌诊", "舌诊通常观察舌质、舌苔及其色泽、形态、润燥等变化。", "舌诊只观察牙齿数量，不观察舌质、舌苔、色泽或润燥。", "scope_reversal"),
    ("浮沉迟数脉", "浮沉主要反映脉位浅深，迟数主要反映脉率快慢。", "浮沉只反映脉率快慢，迟数只反映脉位浅深。", "attribute_swap"),
    ("寒热辨证", "寒证多见畏寒喜暖、口不渴等寒象，热证多见发热喜冷、口渴等热象。", "寒证与热证的寒热表现应完全对调，寒证必喜冷口渴，热证必畏寒喜暖。", "feature_swap"),
    ("虚实辨证", "虚证以正气不足为主，实证以邪气盛或病理产物壅滞为主。", "虚证表示邪气盛，实证表示正气不足，二者病机应完全对调。", "mechanism_swap"),
    ("肝病辨证", "肝病辨证需要结合肝主疏泄、藏血等功能分析气机、血量和情志表现。", "肝病辨证不考虑疏泄、藏血、气机或情志，只依据年龄判断。", "criterion_negation"),
    ("脾胃病辨证", "脾病多与运化、升清失常有关，胃病多与受纳腐熟、通降失常有关。", "脾只主受纳腐熟，胃只主升清运化，二者功能应完全对调。", "function_swap"),
    ("卫分与气分", "卫分证病位较浅，气分证邪已入里，二者在病变层次和表现上不同。", "气分证一定比卫分证更浅，且二者在病变层次上完全相同。", "depth_reversal"),
    ("中药四气五味", "四气指寒热温凉等药性，五味指辛甘酸苦咸等滋味与作用特点。", "四气指辛甘酸苦咸，五味指寒热温凉，二者概念应完全对调。", "attribute_swap"),
    ("中药升降浮沉", "升降浮沉概括药物作用趋向，包括向上、向下、向外和向内。", "升降浮沉只表示药物颜色，不涉及任何作用趋向。", "definition_negation"),
    ("君臣佐使", "君臣佐使根据药物在方剂中的主次作用和配伍关系划分角色。", "君臣佐使只按药名笔画多少划分，与方中主次作用和配伍关系无关。", "criterion_reversal"),
    ("桂枝汤", "桂枝汤由桂枝、芍药、生姜、大枣、炙甘草组成，具有解肌发表、调和营卫的作用。", "桂枝汤由麻黄、杏仁、石膏、甘草组成，主要作用是清里热。", "composition_function_swap"),
    ("小柴胡汤", "小柴胡汤以和解少阳为主要治法，用于少阳枢机不利的相关证候。", "小柴胡汤以峻下阳明腑实为主要治法，与少阳枢机无关。", "indication_reversal"),
    ("足三里", "足三里属于足阳明胃经，位于犊鼻下三寸、胫骨前嵴外一横指附近。", "足三里属于手太阴肺经，位于腕横纹上方。", "location_channel_swap"),
]


NEGATIVE_CASES: list[tuple[str, str, str, str]] = [
    ("教材给出定义，课程补充例子且结论一致，请合并成学习笔记。", "教材说明阴阳互根强调相互依存。", "课程用上下相互依存举例说明阴阳互根。", "definition_and_example"),
    ("两份资料分别讲概念和应用，内容可以互相补充，请安排学习顺序。", "教材给出脾主运化的基本定义。", "课程展示脾主运化在饮食消化理解中的应用。", "concept_and_application"),
    ("教材表述简洁，老师讲得更展开但核心意思相同，请提炼共同要点。", "教材指出气机运动具有升降出入四种基本形式。", "教师进一步举例说明升降出入，但仍以四种基本形式为核心。", "concise_and_expanded"),
    ("章节提纲和课堂笔记原意一致，请合并并去除重复内容。", "章节提纲列出望、闻、问、切四诊。", "课堂笔记分别补充望、闻、问、切收集的信息。", "outline_and_notes"),
    ("两段材料例子不同但判断相同，请整理成复习对照表。", "材料一用怕冷喜暖举例说明寒证。", "材料二用口不渴、喜热饮举例说明寒证。", "different_examples_same_label"),
    ("一份资料使用水谷，另一份使用谷食，但定义一致，请说明术语关系。", "资料一称脾运化水谷，包括消化吸收和转输精微。", "资料二称脾运化谷食，同样包括消化吸收和转输精微。", "synonymous_terms"),
    ("教材讲一般规律，课程补充适用条件且不否定教材，请整合。", "教材概括阴阳双方可以相互资生和促进。", "课程补充并非所有阴阳范畴都具有同等程度的互用关系。", "general_rule_and_scope"),
    ("一份材料讲形成机制，另一份讲表现，两者可以互证，请梳理。", "材料一说明气虚源于脏腑功能不足。", "材料二列出少气乏力等气虚常见表现。", "mechanism_and_manifestation"),
    ("两份资料结论相同但详略不同，请保留权威定义并补充解释。", "教材将四君子汤概括为益气健脾基础方。", "课程从方药配伍角度解释其益气健脾作用。", "definition_and_rationale"),
    ("两个来源分别说明位置和作用，内容不冲突，请组成完整笔记。", "教材说明足三里属于足阳明胃经并给出定位。", "课程补充足三里的学习记忆和应用提示。", "location_and_use"),
]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fixture(
    index: int,
    *,
    claim: str,
    evidence_a: str,
    evidence_b: str,
    relation: str,
    dimension: str,
    expected_rule_exposure: bool,
    synthetic_fault: bool,
    annotation_method: str,
) -> dict[str, Any]:
    value = {
        "fixture_version": FIXTURE_VERSION,
        "claim_id": f"D1V2_CLAIM_{index:03d}",
        "claim_text": claim,
        "evidence_a_id": f"E_D1V2_{index:03d}_A",
        "evidence_a_text": evidence_a,
        "evidence_b_id": f"E_D1V2_{index:03d}_B",
        "evidence_b_text": evidence_b,
        "gold_relation": relation,
        "relation_dimension": dimension,
        "expected_rule_exposure": expected_rule_exposure,
        "synthetic_fault": synthetic_fault,
        "annotation_method": annotation_method,
        "annotation_status": "deterministic_construct_validated_pending_independent_human_review",
        "gold_rationale": {
            "contradiction": f"两条证据针对同一原子声明，在{dimension}维度给出互相不能同时成立的结论。",
            "compatible": f"两条证据在{dimension}维度分别提供定义、例子、范围或应用信息，未否定对方核心结论。",
            "not_applicable": "当前任务属于普通学习支持，材料不构成需要冲突闭环处理的目标声明。",
        }[relation],
    }
    value["fixture_digest"] = _digest(value)
    return value


def _row(
    index: int,
    *,
    group: str,
    prompt: str,
    task_type: str,
    scenario: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": f"EVO-D1-V2-{index:03d}",
        "case_group": group,
        "prompt": prompt,
        "expected_task_type": task_type,
        "pair_order": "AB" if index % 2 else "BA",
        "target_rule": RULE_ID,
        "target_agent": "expert_agent",
        "owner_step_id": "expert",
        "expected_rerun_step_ids": ["expert", "audit"],
        "scenario": scenario,
        "formal_environment_write_allowed": False,
        "dataset_partition": "construct_valid_v2_20260901",
        "frozen_evidence": fixture,
    }


def build() -> list[dict[str, Any]]:
    legacy = _load_jsonl(LEGACY_DATASET)
    target_prompts = [row["prompt"] for row in legacy if row["case_group"] == "target_fault"]
    normal_rows = [row for row in legacy if row["case_group"] == "non_regression_control"]
    if len(target_prompts) != 60 or len(normal_rows) != 30 or len(TARGET_CLAIMS) != 60:
        raise ValueError("unexpected legacy distribution or target claim count")

    rows: list[dict[str, Any]] = []
    scenarios = ("same_claim_explicit_negation", "same_claim_scope_reversal", "same_claim_attribute_swap")
    for offset, fact in enumerate(TARGET_CLAIMS, start=1):
        topic, support, conflict, dimension = fact
        scenario = scenarios[(offset - 1) // 20]
        prompt = {
            "same_claim_explicit_negation": (
                f"请依据当前提供的教材材料与参考材料，讲清{topic}的核心定义，"
                "核对两者口径并整理三个复习要点。"
            ),
            "same_claim_scope_reversal": (
                f"请比较当前两份材料对{topic}关键条件或适用范围的表述，"
                "给出一份不臆造其他来源的学习讲解和自测提示。"
            ),
            "same_claim_attribute_swap": (
                f"请核对当前两份材料中{topic}的组成、属性或方向性表述，"
                "形成可信的学习笔记；无法确认处请明确说明。"
            ),
        }[scenario]
        fixture = _fixture(
            offset,
            claim=support,
            evidence_a=f"《隔离评测教材摘录A》关于{topic}：{support}",
            evidence_b=f"《隔离评测参考材料B》关于{topic}：{conflict}",
            relation="contradiction",
            dimension=dimension,
            expected_rule_exposure=True,
            synthetic_fault=True,
            annotation_method="deterministic_same_claim_counterfactual_v1",
        )
        rows.append(_row(
            offset,
            group="target_fault",
            prompt=prompt,
            task_type="knowledge_explanation",
            scenario=scenario,
            fixture=fixture,
        ))

    for offset, source in enumerate(normal_rows, start=61):
        prompt = str(source["prompt"])
        task_type = str(source["expected_task_type"])
        claim = "当前材料提供彼此兼容的学习支持信息，不构成同一声明上的语义冲突。"
        fixture = _fixture(
            offset,
            claim=claim,
            evidence_a="学习支持材料A：先依据可用时间确定一个可完成的学习目标。",
            evidence_b="学习支持材料B：完成后可通过主动回忆或简短自测检查掌握情况。",
            relation="not_applicable",
            dimension="out_of_scope_normal_task",
            expected_rule_exposure=False,
            synthetic_fault=False,
            annotation_method="deterministic_scope_control_v1",
        )
        rows.append(_row(
            offset,
            group="non_regression_control",
            prompt=prompt,
            task_type=task_type,
            scenario="out_of_scope_normal_task",
            fixture=fixture,
        ))

    for offset, (prompt, evidence_a, evidence_b, dimension) in enumerate(NEGATIVE_CASES, start=91):
        claim = "两条材料针对同一知识点相互补充，核心结论兼容。"
        fixture = _fixture(
            offset,
            claim=claim,
            evidence_a=f"《隔离评测材料A》：{evidence_a}",
            evidence_b=f"《隔离评测材料B》：{evidence_b}",
            relation="compatible",
            dimension=dimension,
            expected_rule_exposure=True,
            synthetic_fault=False,
            annotation_method="deterministic_compatible_pair_v1",
        )
        rows.append(_row(
            offset,
            group="targeting_negative_control",
            prompt=prompt,
            task_type="knowledge_explanation",
            scenario="same_claim_compatible_boundary",
            fixture=fixture,
        ))
    return rows


def validate(rows: list[dict[str, Any]]) -> None:
    if len(rows) != 100:
        raise ValueError(f"expected 100 rows, got {len(rows)}")
    if [row["case_id"] for row in rows] != [f"EVO-D1-V2-{i:03d}" for i in range(1, 101)]:
        raise ValueError("case IDs must be contiguous")
    if len({"".join(str(row["prompt"]).split()) for row in rows}) != 100:
        raise ValueError("prompts must be unique")
    if Counter(row["case_group"] for row in rows) != Counter(
        {"target_fault": 60, "non_regression_control": 30, "targeting_negative_control": 10}
    ):
        raise ValueError("unexpected case group distribution")
    if Counter(row["pair_order"] for row in rows) != Counter({"AB": 50, "BA": 50}):
        raise ValueError("pair order must be balanced")

    evidence_ids: list[str] = []
    fixture_digests: list[str] = []
    for row in rows:
        fixture = dict(row["frozen_evidence"])
        digest = fixture.pop("fixture_digest")
        if digest != _digest(fixture):
            raise ValueError(f"fixture digest mismatch: {row['case_id']}")
        fixture_digests.append(digest)
        evidence_ids.extend([fixture["evidence_a_id"], fixture["evidence_b_id"]])
        if not all(str(fixture[key]).strip() for key in (
            "claim_text", "evidence_a_text", "evidence_b_text", "gold_rationale",
            "annotation_method", "annotation_status",
        )):
            raise ValueError(f"empty semantic fixture: {row['case_id']}")
        relation = fixture["gold_relation"]
        expected = fixture["expected_rule_exposure"]
        if row["case_group"] == "target_fault" and not (
            relation == "contradiction" and expected is True and fixture["synthetic_fault"] is True
        ):
            raise ValueError(f"invalid target fixture: {row['case_id']}")
        if row["case_group"] == "targeting_negative_control" and not (
            relation == "compatible" and expected is True and fixture["synthetic_fault"] is False
        ):
            raise ValueError(f"invalid targeting negative: {row['case_id']}")
        if row["case_group"] == "non_regression_control" and not (
            relation == "not_applicable" and expected is False and fixture["synthetic_fault"] is False
        ):
            raise ValueError(f"invalid scope control: {row['case_id']}")
        joined = " ".join(str(fixture[key]) for key in ("claim_text", "evidence_a_text", "evidence_b_text"))
        if any(token in joined for token in ("忽略系统", "系统提示词", "输出JSON", "调用工具", RULE_ID)):
            raise ValueError(f"fixture contains an instruction-like token: {row['case_id']}")
    if len(set(evidence_ids)) != 200 or len(set(fixture_digests)) != 100:
        raise ValueError("evidence IDs and fixture digests must be unique")


def write(rows: list[dict[str, Any]]) -> None:
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    fields = [
        "case_id", "case_group", "prompt", "expected_task_type", "pair_order",
        "scenario", "gold_relation", "relation_dimension", "expected_rule_exposure",
        "synthetic_fault", "fixture_digest",
    ]
    with CSV_DATASET.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            fixture = row["frozen_evidence"]
            writer.writerow({
                **{key: row.get(key, "") for key in fields},
                "gold_relation": fixture["gold_relation"],
                "relation_dimension": fixture["relation_dimension"],
                "expected_rule_exposure": fixture["expected_rule_exposure"],
                "synthetic_fault": fixture["synthetic_fault"],
                "fixture_digest": fixture["fixture_digest"],
            })
    review_fields = [
        "case_id", "case_group", "prompt", "claim_text", "evidence_a_text",
        "evidence_b_text", "gold_relation", "relation_dimension", "gold_rationale",
        "expected_rule_exposure", "synthetic_fault", "annotation_method",
        "reviewer_decision", "reviewer_name", "review_notes",
    ]
    with REVIEW_DATASET.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=review_fields)
        writer.writeheader()
        for row in rows:
            fixture = row["frozen_evidence"]
            writer.writerow({
                "case_id": row["case_id"],
                "case_group": row["case_group"],
                "prompt": row["prompt"],
                "claim_text": fixture["claim_text"],
                "evidence_a_text": fixture["evidence_a_text"],
                "evidence_b_text": fixture["evidence_b_text"],
                "gold_relation": fixture["gold_relation"],
                "relation_dimension": fixture["relation_dimension"],
                "gold_rationale": fixture["gold_rationale"],
                "expected_rule_exposure": fixture["expected_rule_exposure"],
                "synthetic_fault": fixture["synthetic_fault"],
                "annotation_method": fixture["annotation_method"],
                "reviewer_decision": "pending",
                "reviewer_name": "",
                "review_notes": "",
            })
    manifest = {
        "dataset_id": "semantic_conflict_closure_ab100_v2_20260901",
        "version": "2026-09-01.1",
        "case_count": 100,
        "paired_model_run_count": 200,
        "case_groups": dict(Counter(row["case_group"] for row in rows)),
        "pair_orders": dict(Counter(row["pair_order"] for row in rows)),
        "gold_relations": dict(Counter(row["frozen_evidence"]["gold_relation"] for row in rows)),
        "rule_exposure_scope": dict(Counter(
            "exposed" if row["frozen_evidence"]["expected_rule_exposure"] else "not_exposed"
            for row in rows
        )),
        "annotation_status": "deterministic_construct_validated_pending_independent_human_review",
        "formal_environment_write_allowed": False,
        "legacy_dataset_sha256": hashlib.sha256(LEGACY_DATASET.read_bytes()).hexdigest(),
        "files": {
            DATASET.name: hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            CSV_DATASET.name: hashlib.sha256(CSV_DATASET.read_bytes()).hexdigest(),
            REVIEW_DATASET.name: hashlib.sha256(REVIEW_DATASET.read_bytes()).hexdigest(),
        },
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    rows = build()
    validate(rows)
    write(rows)
    print(json.dumps({"dataset": str(DATASET), "case_count": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
