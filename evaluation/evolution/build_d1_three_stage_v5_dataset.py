from __future__ import annotations

"""Build three leakage-safe datasets for the D1 evolution evaluation.

The stages are deliberately disjoint:
1. discovery: baseline failures used to induce a candidate rule;
2. qualification: unseen cases used to decide whether that rule may enter A/B;
3. final_ab: unseen target, compatible-boundary and ordinary-control cases used
   only for the final four metrics.

Gold annotations live in a separate evaluator-only file. Runtime rows never
contain gold relations, expected rule exposure, conflict IDs, or repair hints.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATASET_DIR = ROOT / "datasets" / "d1_three_stage_v5"
DISCOVERY_DATASET = DATASET_DIR / "d1_rule_discovery_v5.jsonl"
QUALIFICATION_DATASET = DATASET_DIR / "d1_rule_qualification_v5.jsonl"
FINAL_DATASET = DATASET_DIR / "d1_rule_final_ab_v5.jsonl"
GOLD_DATASET = DATASET_DIR / "d1_rule_three_stage_v5.gold.jsonl"
MANIFEST = DATASET_DIR / "d1_rule_three_stage_v5.manifest.json"
DATASET_VERSION = "5.0.0"
TARGET_RULE_FAMILY = "semantic_conflict_pair_closure"


TARGETS: list[dict[str, str]] = [
    {"topic": "阴阳互根与互用", "dimension": "概念边界", "prompt": "阴阳互根和互用到底怎样区分？请结合材料做一张判断题复习卡。", "a": "《基础理论课堂提要》：互根强调阴阳双方在存在上相互依存，互用强调双方在功能上相互资生和促进。", "b": "《题库旧版说明》：凡是存在上相互依存的阴阳双方，都可以直接判为互用，不需要另外判断是否有资生或促进关系。", "rationale": "材料B把互根的成立条件直接等同于互用，扩大了互用范围。"},
    {"topic": "五行相乘的成因", "dimension": "原因范围", "prompt": "五行相乘为什么会发生？请整理成两条容易记住的因果链。", "a": "《五行学说教材》：相乘可因所不胜一方过亢，也可因所胜一方不足，使正常克制相对太过。", "b": "《速记卡》：相乘只有所不胜过亢这一种成因；被克一方不足不会形成相乘。", "rationale": "材料B排除了教材明确列出的第二种成因。"},
    {"topic": "心主血脉", "dimension": "定义组成", "prompt": "心主血脉包括哪些内容？请做成概念卡并给一个易错提醒。", "a": "《藏象教材》：心主血脉包括主血和主脉，既涉及推动血液运行，也涉及维持脉道通利。", "b": "《入门讲义旧稿》：心主血脉只指推动血液运行，脉道是否通利不属于这一概念。", "rationale": "材料B明确排除了材料A定义中的主脉部分。"},
    {"topic": "肺主治节", "dimension": "上位概念范围", "prompt": "肺主治节和呼吸、气机、血液、津液是什么关系？请用层次图说明。", "a": "《藏象课程讲义》：肺主治节是对肺生理作用的高度概括，涉及呼吸、气机、血液运行和津液输布排泄。", "b": "《旧版速记稿》：肺主治节只是调节呼吸频率和节律，与气机、血液和津液无关。", "rationale": "材料B把上位概念缩减成单一呼吸功能。"},
    {"topic": "脾统血", "dimension": "机制关系", "prompt": "脾统血依靠什么机制？请整理成因果链并指出它与升清的关系。", "a": "《脾生理功能教材》：脾统血以脾气固摄血液为核心，升清有助于维持血液正常运行，但二者不能等同。", "b": "《课堂旧笔记》：统血就是升清，二者在机制题中可以互换，不需要说明固摄。", "rationale": "材料B将相关机制错误地表述为完全同一。"},
    {"topic": "肝藏血", "dimension": "静态与动态功能", "prompt": "肝藏血为什么不只是储存？请按安静和活动状态解释。", "a": "《藏象教材》：肝藏血兼有贮藏血液和根据生理活动调节各部分血量的作用。", "b": "《图解初版》：肝藏血只是静息时保存血液，活动时的血量分配不属于肝藏血。", "rationale": "材料B排除了材料A明确包含的动态调节功能。"},
    {"topic": "肾主纳气", "dimension": "呼吸阶段", "prompt": "肾主纳气与吸气、呼气是什么关系？请用通俗比喻加规范表述。", "a": "《呼吸藏象教材》：肾主纳气指肾摄纳肺所吸入的清气，使吸气保持一定深度。", "b": "《旧版笔记》：肾主纳气主要发生在呼气阶段，是把肺呼出的气向下收纳，与吸气无关。", "rationale": "两份材料把纳气定位在相反的呼吸阶段。"},
    {"topic": "元气来源", "dimension": "根源与充养", "prompt": "元气来自先天还是后天？请整理来源和补充方式。", "a": "《气论教材》：元气根于肾中先天之精，并持续得到后天水谷精微的充养。", "b": "《速记表旧版》：元气出生后数量固定，后天水谷精微不能对它进行任何滋养。", "rationale": "材料B否定了材料A明确说明的后天充养。"},
    {"topic": "气为血之帅", "dimension": "口诀展开范围", "prompt": "气为血之帅应展开哪几层意思？请整理成记忆框架。", "a": "《气血关系教材》：气为血之帅可从气能生血、行血和摄血三个方面理解。", "b": "《题库旧口径》：气为血之帅只允许解释为气能行血，生血和摄血不能写入。", "rationale": "两份材料规定了互不相容的口诀展开范围。"},
    {"topic": "津与液", "dimension": "相对与绝对分类", "prompt": "津和液怎样区分又怎样联系？请用表格说明。", "a": "《津液理论教材》：津与液是正常水液的相对区分，二者同源，并可在一定条件下相互转化。", "b": "《分类速记稿》：津和液是位置固定、绝不转化的两种物质，任何情况都可按位置机械判断。", "rationale": "材料B把相对分类改成了不可转化的绝对分类。"},
    {"topic": "经脉与络脉", "dimension": "核心结构标准", "prompt": "经脉和络脉除了粗细还有什么区别？请按结构与功能归纳。", "a": "《经络教材》：经脉是系统主干，络脉是分支网络；深浅只是总体分布倾向，不是唯一标准。", "b": "《旧版简表》：经脉一律在深部、络脉一律在浅表，深浅是没有例外的唯一标准。", "rationale": "材料B把倾向提升为唯一且无例外的规则。"},
    {"topic": "十二经脉流注", "dimension": "教学起点与循环", "prompt": "十二经脉流注为什么从肺经开始讲？请解释循环逻辑。", "a": "《经络课程图注》：十二经流注首尾相贯、如环无端；从肺经开始只是排列和记忆的教学约定。", "b": "《速记图初版》：经气从肺经产生，到肝经即完全停止，肺经是唯一生理起点。", "rationale": "材料B把教学排列点误作循环的绝对起止点。"},
    {"topic": "表证判断", "dimension": "典型表现与必要条件", "prompt": "表证一定同时恶寒发热吗？请整理证据的轻重顺序。", "a": "《八纲辨证教材》：恶寒发热并见是常见特点，但初起时发热可不明显，应结合病程和脉浮等综合判断。", "b": "《旧版判断卡》：没有明显发热就必须排除表证，其他表现不能改变这一结论。", "rationale": "材料B把常见表现错误提升为排他的必要条件。"},
    {"topic": "真假寒热", "dimension": "单一征象排他性", "prompt": "手脚冷一定是寒证吗？请按真假寒热整理判断步骤。", "a": "《寒热辨析讲义》：四肢厥冷可见于真寒，也可能见于热邪深伏形成的热厥，需结合其他表现判断。", "b": "《速判表》：只要四肢厥冷就一定是真寒，可以直接排除里热。", "rationale": "材料B否定了材料A明确列出的热厥可能。"},
    {"topic": "久病虚实", "dimension": "一般趋势与绝对规则", "prompt": "病程久就一定是虚证吗？请说明时间因素应该怎样使用。", "a": "《虚实辨证教材》：新病多实、久病多虚是一般趋势，久病也可邪恋而实或形成虚实夹杂。", "b": "《口诀旧版》：病程超过三个月一律属于虚证，不必再看正邪状态。", "rationale": "材料B把一般趋势改成按时间单独决定的绝对规则。"},
    {"topic": "六经传变", "dimension": "体系顺序与实际路径", "prompt": "六经病按固定顺序发展吗？请整理顺传、合病和并病。", "a": "《六经辨证图注》：六经病可循经传、越经传、表里传，也可见合病、并病和直中。", "b": "《路径图旧版》：病邪必须逐经单向传递，不能越经，也不会同时出现两经证候。", "rationale": "材料B排除了材料A列出的多种实际传变形式。"},
    {"topic": "三焦辨证", "dimension": "解剖位置与功能框架", "prompt": "三焦辨证只是按胸腹位置分上中下吗？请梳理判断框架。", "a": "《温病课程讲义》：三焦辨证借上中下部位组织内容，同时结合脏腑功能和温热病证候层次。", "b": "《定位表初版》：三焦只按三个固定解剖区域划分，与脏腑功能和温病阶段无关。", "rationale": "材料B排除了材料A要求结合的功能与证候层次。"},
    {"topic": "四君子汤原方", "dimension": "原方与替代", "prompt": "四君子汤中人参和党参怎样区分？请按原方与常用替代整理。", "a": "《方剂学课程说明》：原方使用人参；临床可用党参替代，但组成题应标明替代。", "b": "《题库旧版》：党参与人参在所有组成题中完全等同，使用党参仍一律称为原方。", "rationale": "材料B取消了材料A明确要求的原方与替代区分。"},
    {"topic": "四物汤地黄", "dimension": "标准组成与化裁", "prompt": "四物汤用熟地黄还是生地黄？请讲清标准组成和常见变化。", "a": "《课程组成规范》：课程标准组成采用熟地黄；使用生地黄时需按具体化裁或不同文献口径说明。", "b": "《学习单旧版》：生地黄与熟地黄可无条件互换，任写一种都属于相同标准原方。", "rationale": "材料B把有条件的文献或化裁差异改成无条件等同。"},
    {"topic": "逍遥散煎服辅料", "dimension": "组成计分口径", "prompt": "逍遥散中的薄荷和煨姜怎样理解？请区分核心药物与煎服辅料。", "a": "《课程修订说明》：完整组成说明应纳入煨姜和薄荷，并可标注为煎服辅料。", "b": "《组成速记旧版》：薄荷和煨姜绝不算组成，任何组成题写入都按多答处理。", "rationale": "两份材料给出了互斥的组成计分规则。"},
    {"topic": "六味地黄丸比例", "dimension": "口诀与剂量", "prompt": "六味地黄丸六味药的比例怎样记，才不会和简化口诀混淆？", "a": "《方剂比例讲义》：课程采用熟地黄八、山茱萸和山药各四、泽泻牡丹皮茯苓各三的比例。", "b": "《比例题卡初版》：六味药原方全部等量，八四四三三三属于错误答案。", "rationale": "两份材料给出了直接相反的原方比例。"},
    {"topic": "麻黄汤证与桂枝汤证", "dimension": "单项线索与整体辨证", "prompt": "麻黄汤证和桂枝汤证能只看有汗无汗吗？请整理鉴别步骤。", "a": "《方证鉴别讲义》：汗出与否是重要线索，但须结合恶风恶寒轻重和脉浮紧、浮缓等整体表现。", "b": "《二选一口诀》：有汗必是桂枝汤证、无汗必是麻黄汤证，其他表现不影响选择。", "rationale": "材料B把重要线索提升为唯一且无例外的决定条件。"},
    {"topic": "五输穴五行配属", "dimension": "阴阳经次序", "prompt": "五输穴配五行时阴经和阳经分别从哪一行开始？请给记法。", "a": "《腧穴学勘误》：阴经井穴始于木，依次木火土金水；阳经井穴始于金，依次金水木火土。", "b": "《配属表初版》：阴经和阳经完全相同，井穴都从木开始，阳经井穴属金是错误记法。", "rationale": "两份材料对阳经井穴的起始五行给出相反标注。"},
    {"topic": "五味与淡味", "dimension": "术语分类", "prompt": "中药五味中的淡味应该放在哪里？请按术语和做题口径解释。", "a": "《中药学课程说明》：五味专指酸苦甘辛咸；淡味另行说明，且常附于甘。", "b": "《题库旧版》：酸苦甘辛咸淡六项都并列称为五味，题目问五味时必须选择六项。", "rationale": "两份材料采用了互不相容的分类与计分口径。"},
    {"topic": "升降浮沉", "dimension": "固有倾向与条件改变", "prompt": "中药的升降浮沉是固定不变的吗？请整理药性、炮制和配伍的关系。", "a": "《中药学教材》：药物有主要作用趋向，但炮制和配伍可对升降浮沉产生影响。", "b": "《速记稿》：升降浮沉由天然药性永久决定，炮制、配伍和剂型都不能造成改变。", "rationale": "材料B否定了材料A明确说明的条件性改变。"},
    {"topic": "归经", "dimension": "唯一与多重归属", "prompt": "一味药只能归一条经吗？请整理主归经和多归经的理解方式。", "a": "《中药学教材》：归经体现作用选择性，许多药可归两经或多经，并可区分主要和次要范围。", "b": "《旧讲义》：每味药只能归唯一一经，所有兼归或多归经记载都应判错。", "rationale": "两份材料对多归经是否成立给出相反结论。"},
    {"topic": "阴经输穴与原穴", "dimension": "一般定义与特殊重合", "prompt": "阴经为什么会出现输穴和原穴重合？请先讲原穴再讲特殊规律。", "a": "《腧穴分类教材》：原穴是原气经过和留止的腧穴，十二经各有一个原穴。", "b": "《阴经配属讲义》：阴经以输为原，输穴同时承担原穴身份；这是具体配属规律，不改变原穴的一般定义。", "rationale": "两份材料分别说明一般定义与特殊重合，能够同时成立。", "relation": "compatible"},
    {"topic": "肺宣发与皮毛", "dimension": "总体功能与具体表现", "prompt": "肺主宣发和皮毛、汗液有什么联系？请整理成机制链。", "a": "《肺生理功能教材》：宣发是肺气向上向外布散，涉及输布津液和卫气。", "b": "《皮毛专题笔记》：肺气宣发到体表可温养皮毛并参与腠理开合和汗液排泄，这是宣发的具体表现。", "rationale": "材料B是材料A总体功能的具体落点，没有否定材料A。", "relation": "compatible"},
    {"topic": "心主神明与脑", "dimension": "理论层次", "prompt": "心主神明和脑为元神之府怎样一起理解？请按理论层次整理。", "a": "《藏象理论教材》：心主神明概括精神、意识、思维和情志活动与心的整体联系。", "b": "《脑学说专题》：脑是元神活动的重要物质基础，应在整体观下说明脑与心肾协同，而非取消心主神明。", "rationale": "两份材料处于不同理论层次，且材料B明确没有否定材料A。", "relation": "compatible"},
    {"topic": "脾运化两个方面", "dimension": "整体与分项", "prompt": "脾运化水谷和运化水液是两件事还是一个系统？请画出层次。", "a": "《脾藏象教材》：脾主运化包括消化吸收并转输水谷精微和水液。", "b": "《分层讲义》：为便于学习可分为运化水谷与运化水液两个相互联系的方面，并非两个割裂系统。", "rationale": "总定义与教学分项相互补充。", "relation": "compatible"},
    {"topic": "肝疏泄的多种表现", "dimension": "共同机制与不同结果", "prompt": "肝主疏泄为何既影响情绪又影响消化？请用共同机制解释。", "a": "《肝藏象教材》：肝气疏泄有助于全身气机调畅，因而影响情志活动。", "b": "《肝脾关系讲义》：疏泄可协助脾胃气机升降与胆汁排泄，这和调畅情志同属疏调气机的不同表现。", "rationale": "两份材料描述共同机制在不同系统中的表现。", "relation": "compatible"},
    {"topic": "经络主干与分支", "dimension": "上下位结构", "prompt": "经和络怎样组成网络？请从主干、分支和联系作用讲清楚。", "a": "《经络总论》：经脉是经络系统主干，多按一定路径纵行并联系脏腑。", "b": "《络脉专题》：络脉由经脉别出并逐级分支形成广泛网络，不否定经脉的主干地位。", "rationale": "主干与分支是相容的结构关系。", "relation": "compatible"},
    {"topic": "六经名称与分组", "dimension": "集合与分组", "prompt": "六经的六个名称和三阴三阳分组是什么关系？请整理成表格。", "a": "《六经辨证入门》：六经包括太阳、阳明、少阳、太阴、少阴、厥阴。", "b": "《名称记忆表》：前三者合称三阳，后三者合称三阴；分组没有增加或删去任何一经。", "rationale": "一份列集合，一份对同一集合分组。", "relation": "compatible"},
    {"topic": "君臣佐使角色", "dimension": "主要角色与多项作用", "prompt": "一味药在方中只能有一个君臣佐使角色吗？请区分角色和具体作用。", "a": "《方剂结构规范》：初学分析可围绕主治为每味药标一个最主要结构角色。", "b": "《配伍作用讲义》：一味药可以同时产生协同、制约或引经等多项作用，同时仍可按主要贡献标一个核心角色。", "rationale": "主要角色标签与多项具体作用可以同时存在。", "relation": "compatible"},
    {"topic": "药物四气与平性", "dimension": "分类名与补充项", "prompt": "中药四气中为什么还常看到平性？请按术语和学习口径解释。", "a": "《中药学教材》：四气通常指寒、热、温、凉四种基本药性。", "b": "《药性补充讲义》：另有寒热偏性不明显的平性药，说明平性并不要求把“四气”这个术语改名为“五气”。", "rationale": "材料B补充平性，没有否定四气的术语定义。", "relation": "compatible"},
]

DISCOVERY_TARGET_COUNT = 8
QUALIFICATION_TARGET_COUNT = 8
FINAL_TARGET_COUNT = 60
FINAL_BOUNDARY_COUNT = 20
FINAL_NORMAL_COUNT = 20

TARGET_TOPICS = (
    "阴阳互根互用", "五行生克制化", "心藏象", "肺宣发肃降", "脾主运化",
    "肝主疏泄", "肾藏精", "气血津液关系", "经络总论", "八纲辨证",
    "气血辨证", "脏腑辨证", "六经辨证", "卫气营血辨证", "三焦辨证",
    "心气虚与心阳虚", "肝气郁结", "脾气虚", "肾阴虚与肾阳虚", "痰湿证",
    "血瘀证", "风寒感冒", "风热感冒", "四君子汤", "四物汤",
    "逍遥散", "六味地黄丸", "麻黄汤与桂枝汤", "原穴", "五输穴",
)

TARGET_VARIANTS: tuple[dict[str, str], ...] = (
    {
        "dimension": "适用范围",
        "a_template": "《{topic}课程修订稿》：该结论用于总论层面的常见情形；遇到限定条件时，应结合具体证候或题干再判断。",
        "b_template": "《{topic}旧版速记卡》：该结论在所有情形下一律成立，任何限定条件和例外都不能改变答案。",
        "rationale": "材料B把材料A的常见情形扩大成无例外的绝对规则。",
    },
    {
        "dimension": "必要条件",
        "a_template": "《{topic}辨析讲义》：这一表现是重要线索之一，但不能单独决定结论，还需结合其他表现和病程。",
        "b_template": "《{topic}题库旧说明》：只要缺少这一表现，就必须排除该结论，其他证据不能改变判断。",
        "rationale": "材料B把重要线索提升为排他的必要条件。",
    },
    {
        "dimension": "概念边界",
        "a_template": "《{topic}概念课》：相邻概念存在联系，但成立条件和解释层次不同，作答时必须分别说明。",
        "b_template": "《{topic}旧笔记》：两个概念完全等同，在定义题和应用题中都可以无条件互换。",
        "rationale": "材料B将有关联但边界不同的概念错误地表述为完全同一。",
    },
)

BOUNDARY_TOPICS = (
    "手太阴肺经", "足太阴脾经", "手少阴心经", "足厥阴肝经", "足少阴肾经",
    "针刺得气", "艾灸基础", "推拿基础手法", "中医食疗辨证", "中药四气五味",
    "十二经别", "奇经八脉", "募穴与背俞穴", "耳穴基础", "拔罐基础",
    "中药炮制", "药物配伍", "方剂治法", "望诊基础", "脉诊基础",
)


def _generated_target_items() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for variant_number, variant in enumerate(TARGET_VARIANTS, start=1):
        for topic in TARGET_TOPICS:
            items.append(
                {
                    "topic": topic,
                    "dimension": variant["dimension"],
                    "prompt": f"请比较两份关于{topic}的材料，处理其中的口径差异，并整理成第{variant_number}种复习卡。",
                    "a": variant["a_template"].format(topic=topic),
                    "b": variant["b_template"].format(topic=topic),
                    "rationale": variant["rationale"],
                }
            )
    return items


def _generated_boundary_items() -> list[dict[str, str]]:
    return [
        {
            "topic": topic,
            "dimension": "总述与补充",
            "prompt": f"请整合两份关于{topic}的材料，说明共同框架和补充信息。",
            "a": f"《{topic}总论教材》：先给出核心定义、主要结构和学习时应掌握的基本框架。",
            "b": f"《{topic}专题讲义》：在同一框架下补充一个具体表现或应用场景，并明确不否定总论定义。",
            "rationale": "材料B是在材料A共同框架下的具体补充，两者可以同时成立。",
            "relation": "compatible",
        }
        for topic in BOUNDARY_TOPICS
    ]

NORMALS: list[dict[str, str]] = [
    {"prompt": "今晚只有二十分钟复习藏象，怎样安排回忆、核对和自测？", "a": "学习方法材料：先闭卷写出三个核心功能，再打开教材核对遗漏。", "b": "学习方法材料：最后用两道判断题并解释依据，检查是否真正理解。"},
    {"prompt": "两个概念很像时，对比表应该记录哪些信息？", "a": "学习方法材料：分别记录定义、成立条件、典型表现和反例。", "b": "学习方法材料：完成后遮住名称，根据条件和反例反向判断。"},
    {"prompt": "一段教材读完就忘，怎样改造成主动回忆练习？", "a": "学习方法材料：把小节标题改写成问题，合上教材先口头回答。", "b": "学习方法材料：次日随机抽取三问重答，把不完整问题加入复习清单。"},
    {"prompt": "错题原因总写得很笼统，怎样复盘才能确定下一步？", "a": "学习方法材料：把错误拆成概念不清、条件漏看、记忆混淆和推理跳步。", "b": "学习方法材料：每类错误安排一个对应动作，例如重画边界或补做变式题。"},
    {"prompt": "同一个知识点应该隔多久复习？请给一个可调整的方法。", "a": "学习方法材料：初学后在当天、次日和数日后安排短回忆。", "b": "学习方法材料：完整提取就拉长间隔，只能辨认却说不出就缩短间隔。"},
    {"prompt": "视频课笔记总抄太多，怎样只留下真正可复习的内容？", "a": "学习方法材料：只记录章节结构、额外因果解释和没听懂的问题。", "b": "学习方法材料：课后压缩成一页，并补上三个闭卷问题。"},
    {"prompt": "知识点关系很多，画图时怎样避免只有箭头没有含义？", "a": "学习方法材料：每条连线写明导致、包含、条件或易混淆等关系词。", "b": "学习方法材料：沿任一路径用完整句复述，无法复述的连线需补证据或删除。"},
    {"prompt": "复习结束前只剩五分钟，怎样判断今天是否真的学会？", "a": "学习方法材料：不看笔记说出核心定义并举一个适用例。", "b": "学习方法材料：自拟一道最可能答错的判断题并解释答案。"},
    {"prompt": "怎样把一章内容压缩成一页复习卡？", "a": "学习方法材料：只保留核心问题、答案关键词和关系图。", "b": "学习方法材料：每个板块附一个可闭卷回答的自测问题。"},
    {"prompt": "我总是在选择题里漏看条件，应该怎样训练？", "a": "学习方法材料：先圈出时间、范围、程度和否定词，再判断考点。", "b": "学习方法材料：复盘时记录改变哪个条件会让答案翻转。"},
    {"prompt": "怎样判断自己只是记住了，还是已经理解了？", "a": "学习方法材料：能脱离原文解释概念及成立条件，才不只是识别。", "b": "学习方法材料：用新例子应用并解释反例，可进一步检验理解。"},
    {"prompt": "一周复习计划总坚持不下来，怎样缩小到可执行？", "a": "学习方法材料：每天只设一个最小完成单元并限定时长。", "b": "学习方法材料：完成后记录一次主动回忆结果，而不是记录阅读时长。"},
    {"prompt": "老师讲的例子很多，我应该怎样选择值得记的例子？", "a": "学习方法材料：优先保留能显示成立条件或概念边界的例子。", "b": "学习方法材料：同时保留一个反例，避免把典型例子当成唯一情况。"},
    {"prompt": "做完题后应该立刻看答案还是先自己解释？", "a": "学习方法材料：先写出选择理由和排除依据，再核对答案。", "b": "学习方法材料：核对后只改错误的推理环节，不整段照抄解析。"},
    {"prompt": "怎样用口头复述检查一张概念图？", "a": "学习方法材料：按节点与关系词组成完整句，从起点复述到终点。", "b": "学习方法材料：若某条边只能说“有关”，应补充具体关系或删除。"},
    {"prompt": "一套题错得很多时，应该按题号还是按原因复盘？", "a": "学习方法材料：先按错误原因归类，再在每类中保留代表题。", "b": "学习方法材料：相同原因连续出现时只设一个针对性训练动作。"},
    {"prompt": "怎样在睡前十分钟做低负担复习？", "a": "学习方法材料：选择三个当天核心问题进行无提示回忆。", "b": "学习方法材料：只核对遗漏点，并把最弱的一问留到次日。"},
    {"prompt": "背诵内容很长，怎样拆成能自测的小块？", "a": "学习方法材料：按定义、条件、过程和例外拆成若干问答单元。", "b": "学习方法材料：每个单元控制在一次能完整复述并核对的范围。"},
    {"prompt": "我会定义但不会做题，下一步应该练什么？", "a": "学习方法材料：先练识别成立条件和不成立的反例。", "b": "学习方法材料：再练改变一个条件后重新判断的变式题。"},
    {"prompt": "怎样让复习清单不越积越长？", "a": "学习方法材料：只保留尚不能主动提取或应用的项目。", "b": "学习方法材料：连续两次完整作答的项目移出活跃清单，改为延迟复查。"},
]


def _sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _target_runtime(
    stage: str,
    number: int,
    item: dict[str, str],
    *,
    id_group: str = "T",
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = f"D1V5-{stage.upper()}-{id_group}{number:03d}"
    runtime = {
        "schema_version": "d1-three-stage-runtime-1.0",
        "case_id": case_id,
        "stage": stage,
        "case_group": "target_fault" if item.get("relation", "contradiction") == "contradiction" else "compatible_boundary",
        "prompt": item["prompt"],
        "task_type": "knowledge_explanation",
        "materials": [
            {"material_id": f"{case_id}-M1", "text": item["a"]},
            {"material_id": f"{case_id}-M2", "text": item["b"]},
        ],
        "formal_environment_write_allowed": False,
    }
    gold = {
        "schema_version": "d1-three-stage-gold-1.0",
        "case_id": case_id,
        "stage": stage,
        "gold_relation": item.get("relation", "contradiction"),
        "relation_dimension": item["dimension"],
        "gold_rationale": item["rationale"],
        "source_status": "synthetic_evaluation_fixture_pending_independent_human_review",
    }
    return runtime, gold


def _normal_runtime(number: int, item: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = f"D1V5-FINAL-N{number:03d}"
    runtime = {
        "schema_version": "d1-three-stage-runtime-1.0",
        "case_id": case_id,
        "stage": "final_ab",
        "case_group": "ordinary_control",
        "prompt": item["prompt"],
        "task_type": "general_learning_support",
        "materials": [
            {"material_id": f"{case_id}-M1", "text": item["a"]},
            {"material_id": f"{case_id}-M2", "text": item["b"]},
        ],
        "formal_environment_write_allowed": False,
    }
    gold = {
        "schema_version": "d1-three-stage-gold-1.0",
        "case_id": case_id,
        "stage": "final_ab",
        "gold_relation": "not_applicable",
        "relation_dimension": "ordinary_learning_support",
        "gold_rationale": "两条材料提供可共同采用的学习建议，不存在需要解决的知识声明冲突。",
        "source_status": "synthetic_evaluation_fixture_pending_independent_human_review",
    }
    return runtime, gold


def build() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    generated_targets = _generated_target_items()
    generated_boundaries = _generated_boundary_items()
    target_pool = [item for item in TARGETS if item.get("relation", "contradiction") == "contradiction"] + generated_targets
    if len(target_pool) < DISCOVERY_TARGET_COUNT + QUALIFICATION_TARGET_COUNT + FINAL_TARGET_COUNT:
        raise ValueError("unexpected source pool size")
    if len(generated_boundaries) != FINAL_BOUNDARY_COUNT or len(NORMALS) != FINAL_NORMAL_COUNT:
        raise ValueError("unexpected control pool size")
    discovery_items = target_pool[:DISCOVERY_TARGET_COUNT]
    qualification_start = DISCOVERY_TARGET_COUNT
    qualification_end = qualification_start + QUALIFICATION_TARGET_COUNT
    qualification_items = target_pool[qualification_start:qualification_end]
    final_target_items = target_pool[qualification_end:qualification_end + FINAL_TARGET_COUNT]
    final_boundary_items = generated_boundaries

    stages: dict[str, list[dict[str, Any]]] = {
        "discovery": [],
        "qualification": [],
        "final_ab": [],
    }
    gold: list[dict[str, Any]] = []
    for stage, items in (("discovery", discovery_items), ("qualification", qualification_items)):
        for index, item in enumerate(items, start=1):
            runtime, annotation = _target_runtime(stage, index, item)
            stages[stage].append(runtime)
            gold.append(annotation)
    for index, item in enumerate(final_target_items, start=1):
        runtime, annotation = _target_runtime("final", index, item, id_group="T")
        runtime["stage"] = "final_ab"
        runtime["pair_order"] = "AB" if index % 2 else "BA"
        annotation["stage"] = "final_ab"
        stages["final_ab"].append(runtime)
        gold.append(annotation)
    for index, item in enumerate(final_boundary_items, start=1):
        runtime, annotation = _target_runtime("final", index, item, id_group="B")
        runtime["stage"] = "final_ab"
        runtime["pair_order"] = "AB" if index % 2 else "BA"
        annotation["stage"] = "final_ab"
        stages["final_ab"].append(runtime)
        gold.append(annotation)
    for index, item in enumerate(NORMALS, start=1):
        runtime, annotation = _normal_runtime(index, item)
        runtime["pair_order"] = "AB" if index % 2 else "BA"
        stages["final_ab"].append(runtime)
        gold.append(annotation)
    return stages, gold


def validate(stages: dict[str, list[dict[str, Any]]], gold: list[dict[str, Any]]) -> None:
    expected = {
        "discovery": DISCOVERY_TARGET_COUNT,
        "qualification": QUALIFICATION_TARGET_COUNT,
        "final_ab": FINAL_TARGET_COUNT + FINAL_BOUNDARY_COUNT + FINAL_NORMAL_COUNT,
    }
    if {name: len(rows) for name, rows in stages.items()} != expected:
        raise ValueError("unexpected stage distribution")
    all_rows = [row for rows in stages.values() for row in rows]
    case_ids = [row["case_id"] for row in all_rows]
    if len(case_ids) != len(set(case_ids)) or len(gold) != len(all_rows):
        raise ValueError("case IDs or gold coverage invalid")
    if {row["case_id"] for row in gold} != set(case_ids):
        raise ValueError("gold/runtime case coverage mismatch")
    prompt_keys = ["".join(row["prompt"].split()) for row in all_rows]
    if len(prompt_keys) != len(set(prompt_keys)):
        raise ValueError("prompts must be disjoint across all stages")
    material_keys = [
        _sha([material["text"] for material in row["materials"]])
        for row in all_rows
    ]
    if len(material_keys) != len(set(material_keys)):
        raise ValueError("material pairs must be disjoint across all stages")
    forbidden = {"gold_relation", "gold_rationale", "expected_rule_exposure", "conflict_binding", "repair_instruction", "target_rule"}
    for row in all_rows:
        if forbidden.intersection(row):
            raise ValueError(f"runtime row leaks evaluator fields: {row['case_id']}")
        if row["formal_environment_write_allowed"] is not False:
            raise ValueError("writeback must remain disabled")
    final_groups = Counter(row["case_group"] for row in stages["final_ab"])
    if final_groups != Counter({"target_fault": 60, "compatible_boundary": 20, "ordinary_control": 20}):
        raise ValueError("unexpected final A/B groups")
    if Counter(row["pair_order"] for row in stages["final_ab"]) != Counter({"AB": 50, "BA": 50}):
        raise ValueError("final pair order must be balanced")
    gold_relations = Counter(row["gold_relation"] for row in gold)
    if gold_relations != Counter({"contradiction": 76, "compatible": 20, "not_applicable": 20}):
        raise ValueError("unexpected gold relation distribution")


def write(stages: dict[str, list[dict[str, Any]]], gold: list[dict[str, Any]]) -> None:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    paths = {
        "discovery": DISCOVERY_DATASET,
        "qualification": QUALIFICATION_DATASET,
        "final_ab": FINAL_DATASET,
    }
    for name, path in paths.items():
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in stages[name]), encoding="utf-8")
    GOLD_DATASET.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in gold), encoding="utf-8")
    files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [*paths.values(), GOLD_DATASET]}
    manifest = {
        "schema_version": "d1-three-stage-manifest-1.0",
        "dataset_id": "d1_three_stage_v5",
        "version": DATASET_VERSION,
        "target_rule_family": TARGET_RULE_FAMILY,
        "stages": {
            "discovery": {"case_count": 8, "use": "baseline-only failure discovery; never score final effect"},
            "qualification": {"case_count": 8, "use": "unseen candidate-rule admission gate; never score final effect"},
            "final_ab": {
                "case_count": 100,
                "paired_generation_count": 200,
                "groups": {"target_fault": 60, "compatible_boundary": 20, "ordinary_control": 20},
                "pair_orders": {"AB": 50, "BA": 50},
                "use": "strict single-treatment production A/B and four final metrics",
            },
        },
        "runtime_gold_separated": True,
        "cross_stage_prompt_and_material_overlap": 0,
        "formal_environment_write_allowed": False,
        "annotation_status": "synthetic_evaluation_fixture_pending_independent_human_review",
        "files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    stages, gold = build()
    validate(stages, gold)
    write(stages, gold)
    print(json.dumps({"valid": True, "stages": {name: len(rows) for name, rows in stages.items()}, "manifest": str(MANIFEST)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
