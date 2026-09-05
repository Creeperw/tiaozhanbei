from __future__ import annotations

"""Build the frozen, pre-registered D1 V5.1 advantage-scenario dataset.

V5.0 remains untouched.  This version deliberately enriches the target
population for the discovered failure mechanism: a learner asks for one
synthesis of two same-topic materials that contain directly incompatible
claims, while no authority/version metadata permits choosing a winner.
Selection is completed before any qualification or final execution; no case
may be removed based on observed arm outcomes.
"""

from collections import Counter
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from build_d1_three_stage_v5_dataset import NORMALS, TARGETS


ROOT = Path(__file__).resolve().parent
DATASET_DIR = ROOT / "datasets" / "d1_three_stage_v5_advantage"
DISCOVERY_DATASET = DATASET_DIR / "d1_rule_discovery_advantage_v5_1.jsonl"
QUALIFICATION_DATASET = DATASET_DIR / "d1_rule_qualification_advantage_v5_1.jsonl"
FINAL_DATASET = DATASET_DIR / "d1_rule_final_ab_advantage_v5_1.jsonl"
GOLD_DATASET = DATASET_DIR / "d1_rule_three_stage_advantage_v5_1.gold.jsonl"
MANIFEST = DATASET_DIR / "d1_rule_three_stage_advantage_v5_1.manifest.json"
DATASET_ID = "d1_three_stage_v5_advantage"
DATASET_VERSION = "5.1.0"
TARGET_RULE_FAMILY = "model_authored_conflict_authority_boundary"

DISCOVERY_TARGET_COUNT = 8
QUALIFICATION_TARGET_COUNT = 8
FINAL_TARGET_COUNT = 60
FINAL_BOUNDARY_COUNT = 20
FINAL_NORMAL_COUNT = 20

# These are authored before execution and are not generated from model output.
# Each pair encodes a direct logical contradiction rather than a disputed
# clinical recommendation.  Gold remains pending independent human review.
ADDITIONAL_TARGETS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "脾气升清与升提",
        "功能范围",
        "脾气升清和升提内脏应该怎样区分？请整理成概念卡。",
        "脾气升清主要指将水谷精微向上输布，升提内脏是这一升举作用在维持脏器位置方面的表现。",
        "脾气升清只负责维持脏器位置，与水谷精微向上输布没有关系。",
        "材料乙排除了材料甲明确包含的精微输布功能。",
    ),
    (
        "肺朝百脉",
        "参与范围",
        "肺朝百脉是否等于肺直接生成血液？请归纳概念边界。",
        "肺朝百脉强调全身血液汇聚于肺并通过肺的呼吸参与气体交换，再随血脉输布全身。",
        "肺朝百脉就是肺直接制造全部血液，不涉及血液汇聚或呼吸交换。",
        "两份材料对肺朝百脉的核心含义给出互斥解释。",
    ),
    (
        "肾主水",
        "调节机制",
        "肾主水包含哪些环节？请做一张机制链复习卡。",
        "肾对水液代谢的调节涉及气化、开合以及对相关脏腑水液活动的推动。",
        "肾主水只表示肾储存已经形成的尿液，不参与水液气化和开合。",
        "材料乙否定了材料甲所述的气化与开合调节。",
    ),
    (
        "心藏神",
        "概念范围",
        "心藏神在藏象理论中怎样理解？请给出规范表述。",
        "心藏神用于概括精神、意识、思维和情志活动与心的整体联系。",
        "心藏神只指睡眠状态，与意识、思维和情志活动无关。",
        "材料乙把材料甲的整体概念缩减为单一睡眠状态。",
    ),
    (
        "肝主筋",
        "功能联系",
        "肝主筋与筋的运动有什么关系？请整理成因果链。",
        "筋的正常屈伸活动有赖于肝血濡养，肝血充足有助于运动灵活。",
        "肝主筋只说明筋附着在肝周围，与肝血濡养及屈伸运动无关。",
        "两份材料对肝主筋是否涉及濡养和运动给出相反结论。",
    ),
    (
        "肺合皮毛",
        "内外联系",
        "肺与皮毛的联系应该怎样记？请整理成两层要点。",
        "肺通过宣发卫气和津液温养皮毛，并参与腠理开合。",
        "肺与皮毛不存在功能联系，腠理开合完全不受肺气影响。",
        "材料乙直接否定材料甲建立的肺与皮毛功能联系。",
    ),
    (
        "脾主肌肉四肢",
        "营养来源",
        "脾与肌肉、四肢有什么联系？请做成复习提纲。",
        "肌肉和四肢的充养有赖于脾运化产生并输布的水谷精微。",
        "脾只影响食欲，肌肉和四肢的充养与脾运化无关。",
        "材料乙否定了材料甲所述的运化与肌肉四肢充养关系。",
    ),
    (
        "肾主骨生髓",
        "概念组成",
        "肾主骨、生髓和脑髓之间怎样联系？请画出层次。",
        "肾精可化生骨髓并充养骨骼，髓又包括骨髓、脊髓和脑髓的理论联系。",
        "肾主骨只涉及骨的外形，与髓的化生和脑髓没有任何联系。",
        "材料乙排除了材料甲概念中的生髓及脑髓联系。",
    ),
    (
        "三焦通行元气",
        "通道功能",
        "三焦与元气运行有什么关系？请归纳成一句定义和两点说明。",
        "三焦可作为元气运行的通道，使元气布达全身。",
        "三焦只运输饮食残渣，不能通行元气。",
        "材料乙直接否定材料甲所述的元气通道功能。",
    ),
    (
        "奇经八脉作用",
        "调节范围",
        "奇经八脉对十二经脉有什么作用？请整理成关系图。",
        "奇经八脉可联络、组合十二经脉，并对经脉气血起蓄积和调节作用。",
        "奇经八脉与十二经脉完全隔绝，既不联络也不调节经脉气血。",
        "两份材料对奇经八脉是否联络和调节十二经脉给出相反结论。",
    ),
    (
        "任脉与督脉",
        "阴阳经脉联系",
        "任脉和督脉分别怎样联系阴经与阳经？请做成对照卡。",
        "任脉与诸阴经相联系，有阴脉之海之称；督脉与诸阳经相联系，有阳脉之海之称。",
        "任脉统摄所有阳经而督脉统摄所有阴经，两者称号应当互换。",
        "材料乙将材料甲中的任督阴阳归属完全对调。",
    ),
    (
        "十二经别",
        "表里联系",
        "十二经别如何加强表里经联系？请用路径要点说明。",
        "十二经别从十二经脉别出，深入体腔并加强相为表里的两经在深部的联系。",
        "十二经别只分布在体表，不能进入体腔，也不加强表里经联系。",
        "材料乙逐项否定材料甲描述的循行和联系作用。",
    ),
    (
        "原穴数量",
        "配置规则",
        "十二经脉的原穴怎样配置？请说明阴经的特殊规律。",
        "十二经脉各有一个原穴，阴经常以输穴兼作原穴。",
        "只有阳经有原穴，阴经既没有原穴也不存在输原重合。",
        "材料乙否定了材料甲所述的阴经原穴及输原重合。",
    ),
    (
        "郄穴",
        "功能倾向",
        "郄穴的含义和常见应用倾向怎样理解？请做概念卡。",
        "郄穴是经脉气血深聚的部位，常用于相关经脉或脏腑的急性病证。",
        "郄穴是气血完全不经过的空隙，只用于慢性虚证，不能用于急性病证。",
        "材料乙对气血深聚及急性病证应用作了直接否定。",
    ),
    (
        "八会穴",
        "会穴对象",
        "八会穴分别会聚哪些对象？请整理记忆框架。",
        "八会穴是脏、腑、气、血、筋、脉、骨、髓精气会聚的八个腧穴。",
        "八会穴只对应八条经脉，与脏腑气血筋脉骨髓无关。",
        "两份材料对八会穴的会聚对象给出互斥定义。",
    ),
    (
        "背俞穴与募穴",
        "分布位置",
        "背俞穴和募穴怎样按位置区分？请整理成表格。",
        "背俞穴主要位于背腰部，募穴主要位于胸腹部，两类穴位可配合应用。",
        "募穴全部位于背腰部，背俞穴全部位于胸腹部，两类位置应完全对调。",
        "材料乙把材料甲所述的两类穴位位置完全对调。",
    ),
    (
        "五输穴次序",
        "排列顺序",
        "五输穴从四肢末端向肘膝排列的次序是什么？请给记法。",
        "五输穴从四肢末端向肘膝依次为井、荥、输、经、合。",
        "五输穴从四肢末端向肘膝依次为合、经、输、荥、井。",
        "两份材料给出了方向相同但次序完全相反的排列。",
    ),
    (
        "针刺得气",
        "判断依据",
        "得气应该由谁的感觉判断？请整理医者与受术者两方面表现。",
        "得气可同时表现为受术者的酸麻胀重等感觉和医者针下的沉紧感。",
        "得气只能由医者看到针柄振动判断，受术者感觉和针下感都不能作为依据。",
        "材料乙排除了材料甲列出的两类主要判断表现。",
    ),
    (
        "迎随补泻",
        "方向原则",
        "迎随补泻中迎和随怎样对应补泻？请整理成记忆卡。",
        "针尖顺着经脉循行方向称随，常用于补法；逆着循行方向称迎，常用于泻法。",
        "顺经而刺一律称迎并用于泻，逆经而刺一律称随并用于补。",
        "材料乙将材料甲的迎随名称与方向完全对调。",
    ),
    (
        "艾灸温通作用",
        "作用性质",
        "艾灸的温通作用怎样理解？请归纳原理和边界。",
        "艾灸以温热刺激发挥温经散寒、行气通络等作用。",
        "艾灸属于纯冷刺激，不能产生温经散寒或通络作用。",
        "材料乙直接否定材料甲所述的温热性质与温通作用。",
    ),
    (
        "舌色淡白与红绛",
        "寒热虚实倾向",
        "淡白舌和红绛舌的常见意义怎样区分？请做对照表。",
        "淡白舌多提示气血不足或阳虚寒盛，红绛舌多提示热盛或阴虚火旺。",
        "淡白舌只提示实热，红绛舌只提示阳虚寒盛，两者意义应互换。",
        "材料乙把材料甲列出的两类舌色意义完全对调。",
    ),
    (
        "舌苔厚薄",
        "病位与邪气",
        "舌苔厚薄通常反映什么？请说明不能机械判断的地方。",
        "薄苔常见于正常或病邪较浅，厚苔常提示邪气较盛或病位较深，但仍需综合判断。",
        "苔越薄必然病越重，苔越厚必然完全健康，任何其他表现都无需考虑。",
        "材料乙对厚薄苔的一般意义作出反向且绝对化的解释。",
    ),
    (
        "浮脉与沉脉",
        "取脉层次",
        "浮脉和沉脉怎样按取脉层次区分？请整理手下感觉。",
        "浮脉轻取即得、重按稍减，沉脉轻取不应而重按始得。",
        "浮脉必须重按才出现，沉脉轻触皮肤就最明显。",
        "材料乙将材料甲所述的浮沉取脉层次完全对调。",
    ),
    (
        "迟脉与数脉",
        "频率方向",
        "迟脉和数脉的频率特点怎样记？请做一张最小复习卡。",
        "迟脉脉率较慢，数脉脉率较快，具体判断还需结合年龄和状态。",
        "迟脉表示脉率加快，数脉表示脉率减慢，名称与速度应反向理解。",
        "两份材料对迟数脉的速度方向给出相反结论。",
    ),
    (
        "虚脉与实脉",
        "有力无力",
        "虚脉和实脉的总体手感怎样区分？请归纳共同判断维度。",
        "虚脉总体以无力为主要特征，实脉总体以有力为主要特征。",
        "虚脉必然强劲有力，实脉必然软弱无力，两者不能按通常含义理解。",
        "材料乙将材料甲所述的有力与无力特征完全对调。",
    ),
    (
        "六淫致病",
        "发生条件",
        "风寒暑湿燥火何时称为六淫？请区分自然气候与病因。",
        "正常六气在太过、不及或非其时并导致人体发病时，才称为六淫。",
        "任何正常气候变化都一律属于六淫，即使没有导致人体发病也不例外。",
        "材料乙取消了材料甲明确要求的异常和致病条件。",
    ),
    (
        "风性善行数变",
        "变化特点",
        "风邪善行数变应该怎样用通俗例子理解？请先给规范含义。",
        "善行指病位游走不定，数变指发病急、变化快。",
        "善行表示病位固定不移，数变表示病情多年不发生变化。",
        "材料乙对善行和数变的含义均作出直接反向解释。",
    ),
    (
        "湿性重浊",
        "症状倾向",
        "湿性重浊中的重和浊分别指什么？请整理成两列。",
        "湿邪致病常见沉重、困倦等重着表现，并可见分泌物秽浊等浊的特点。",
        "湿邪只产生轻扬清爽表现，不会出现沉重困倦或秽浊分泌物。",
        "材料乙直接否定材料甲所述的重着和秽浊表现。",
    ),
    (
        "燥邪伤津",
        "主要损伤",
        "燥邪为什么容易出现干燥表现？请整理病因到表现的链条。",
        "燥性干涩，容易耗伤津液，因而可见口鼻、皮肤等干燥表现。",
        "燥邪主要增加津液并使组织过度湿润，不会导致任何干燥表现。",
        "材料乙对燥邪与津液、干燥表现的关系作出完全相反说明。",
    ),
    (
        "七情内伤",
        "致病条件",
        "喜怒忧思悲恐惊何时会成为病因？请说明正常情志和内伤的边界。",
        "正常情志活动不直接致病，突然、强烈或持久并超过调节能力时可成为内伤病因。",
        "任何轻微且短暂的正常情绪都会必然致病，不存在非致病的情志活动。",
        "材料乙取消了材料甲对强度、持续时间和调节能力的条件限制。",
    ),
    (
        "痰与饮",
        "稠浊清稀",
        "痰和饮在形态与停聚方面怎样区分？请做概念对照。",
        "痰多较稠浊，饮多较清稀，二者都是水液代谢障碍形成的病理产物。",
        "痰只能是清稀水液，饮只能是稠厚固体，两者没有共同形成基础。",
        "材料乙对痰饮形态和共同形成基础均作出相反说明。",
    ),
    (
        "血瘀与出血",
        "可能关系",
        "血瘀为什么有时会伴随出血？请整理机制而不是只背症状。",
        "瘀血阻滞可使血行不循常道，因此血瘀既可见疼痛、肿块，也可能出现出血。",
        "只要发生出血就能绝对排除血瘀，血瘀在任何情况下都不会伴随出血。",
        "材料乙否定了材料甲明确列出的血瘀伴随出血可能。",
    ),
    (
        "气滞与疼痛",
        "疼痛特点",
        "气滞疼痛通常有什么特点？请说明活动或情志变化的影响。",
        "气滞疼痛常见胀痛、部位游走或随情志变化而增减。",
        "气滞疼痛必定固定刺痛且昼夜完全不变，不会受情志影响。",
        "材料乙用互斥特征替换了材料甲所述的气滞疼痛特点。",
    ),
    (
        "气虚与推动功能",
        "功能状态",
        "气虚为什么会出现乏力？请从气的推动作用解释。",
        "气虚时推动和激发作用不足，可见乏力、少气等表现。",
        "气虚表示气的推动作用异常增强，因此只会出现过度兴奋而不会乏力。",
        "材料乙对气虚时推动作用强弱及乏力关系作出反向解释。",
    ),
    (
        "血虚与濡养",
        "濡养状态",
        "血虚表现为什么常和失于濡养有关？请整理机制链。",
        "血虚时血液濡养功能不足，可出现面色、唇甲淡白或头晕等表现。",
        "血虚表示濡养功能过度旺盛，面色淡白和头晕与血虚完全无关。",
        "材料乙否定材料甲所述的濡养不足机制及常见表现。",
    ),
    (
        "阳虚与寒象",
        "寒热倾向",
        "阳虚为什么常见寒象？请先讲机制再列典型线索。",
        "阳气温煦作用减弱时可出现畏寒、肢冷等虚寒表现。",
        "阳虚只会导致高热和喜冷，绝不出现畏寒或肢冷。",
        "材料乙对阳虚的温煦状态和寒热表现作出相反结论。",
    ),
    (
        "阴虚与虚热",
        "寒热倾向",
        "阴虚为什么可能出现虚热？请整理成简短因果链。",
        "阴液亏少、制约阳热的作用不足时，可出现潮热、五心烦热等虚热表现。",
        "阴虚必然只出现实寒，不能出现任何虚热表现。",
        "材料乙直接否定材料甲所述的阴虚虚热机制。",
    ),
    (
        "正治与反治",
        "治法方向",
        "正治和反治怎样按现象与本质区分？请做一张概念表。",
        "正治采用与疾病表面证候性质相反的治法，反治顺从表面假象而针对疾病本质。",
        "正治必须顺从所有表面假象，反治只处理表面而不考虑疾病本质。",
        "材料乙将材料甲所述的正治反治方向与本质关系颠倒。",
    ),
    (
        "标本缓急",
        "处理顺序",
        "治病求本是否意味着任何时候都先治本？请整理缓急原则。",
        "通常强调治病求本，但标病危急时可先治其标，待急势缓解再图治本。",
        "无论标病多么危急都绝不能先治标，只要先处理紧急表现就一定错误。",
        "材料乙取消了材料甲明确说明的标急先治例外。",
    ),
    (
        "扶正与祛邪",
        "并用关系",
        "扶正和祛邪只能二选一吗？请按正邪盛衰整理。",
        "扶正与祛邪可依据正邪盛衰分别使用，也可在兼顾不留邪、不伤正时合用。",
        "扶正和祛邪在任何情况下都不能合用，二者必然互相抵消。",
        "材料乙否定了材料甲明确允许的有条件合用。",
    ),
    (
        "汗法",
        "适用病位",
        "汗法的基本作用和主要适用病位是什么？请整理治法卡。",
        "汗法主要通过发汗解表，使在表之邪随汗而解。",
        "汗法只用于排出深部有形积滞，与表证和解表无关。",
        "两份材料对汗法的作用方式和主要病位给出互斥说明。",
    ),
    (
        "和法",
        "治疗特点",
        "和法为什么不同于单纯攻下或发汗？请归纳其调和特点。",
        "和法通过和解少阳、调和脏腑等方式，使表里寒热或脏腑关系趋于协调。",
        "和法就是峻烈攻下，不能用于任何调和表里或脏腑关系。",
        "材料乙直接否定材料甲所述的调和性质。",
    ),
    (
        "下法",
        "作用方向",
        "下法主要通过什么途径祛除病邪？请区分不同类别。",
        "下法通过泻下、攻逐等使停留肠胃的有形积滞从下而出。",
        "下法只通过向上涌吐排邪，不能使肠胃积滞从下而出。",
        "材料乙把下法的主要排邪方向改成与材料甲相反的向上涌吐。",
    ),
    (
        "温法",
        "寒热方向",
        "温法的基本目的是什么？请整理适用证候的共同点。",
        "温法以温里祛寒、回阳救逆或温经散寒等方式治疗寒证。",
        "温法的唯一作用是清热泻火，只用于热证而绝不用于寒证。",
        "两份材料对温法的寒热方向和适用证候给出相反结论。",
    ),
    (
        "清法",
        "寒热方向",
        "清法包含哪些清热思路？请整理成治法框架。",
        "清法以清热、泻火、凉血或解毒等方式治疗里热证。",
        "清法只用于温补阳气和驱散里寒，不能用于里热证。",
        "材料乙将清法的作用方向解释为与材料甲相反的温补散寒。",
    ),
    (
        "补法",
        "治疗对象",
        "补法是不是只等于补气？请整理常见补益方向。",
        "补法用于补益人体气、血、阴、阳等不足，并不只限于补气。",
        "补法只能补气，血虚、阴虚和阳虚都绝对不能使用补益思路。",
        "材料乙把材料甲的多类补益对象缩减为唯一补气。",
    ),
    (
        "消法",
        "作用方式",
        "消法与峻下有什么区别？请说明渐消和直接排出的差异。",
        "消法重在消导或化积，使有形积滞渐消缓散，不等同于峻烈攻下。",
        "消法就是一次性峻烈攻下，任何渐消缓散都不属于消法。",
        "材料乙把材料甲明确区分的消法与峻下表述为完全相同。",
    ),
    (
        "君药与臣药",
        "主次作用",
        "君药和臣药怎样按针对主证的贡献区分？请做配伍角色卡。",
        "君药针对主病或主证起主要治疗作用，臣药可加强君药或兼治重要兼证。",
        "臣药必须取代君药成为唯一主要药，君药只能处理无关次要表现。",
        "材料乙将材料甲所述的君臣主要与辅助关系完全颠倒。",
    ),
    (
        "四气",
        "寒热属性",
        "寒热温凉四气怎样按作用倾向理解？请整理对照轴。",
        "寒凉药与温热药的作用倾向相对，分别适用于性质相反的寒热证候。",
        "寒凉与温热在作用倾向上完全相同，不需要区分寒证和热证。",
        "材料乙否定了材料甲所述的四气相对作用倾向。",
    ),
    (
        "酸味与涩味",
        "作用异同",
        "酸味和涩味为什么常放在一起比较？请说明相似与边界。",
        "酸味与涩味均有收敛固涩倾向，但来源与具体药物作用仍需分别掌握。",
        "酸味和涩味都只具有发散作用，绝无收敛固涩倾向。",
        "材料乙直接否定材料甲所述的共同收敛固涩倾向。",
    ),
    (
        "苦味作用",
        "作用方向",
        "苦味常概括哪些作用？请整理泄、燥、坚三类记法。",
        "苦味常概括能泄、能燥、能坚等作用方向。",
        "苦味只能上升发散，泄、燥、坚都不属于苦味作用。",
        "材料乙排除了材料甲列出的苦味三类主要作用。",
    ),
    (
        "甘味作用",
        "作用方向",
        "甘味为什么既能补又能缓？请整理核心作用。",
        "甘味常有补益、和中、调和药性和缓急等作用。",
        "甘味只能峻下攻积，不能补益、和中、调和或缓急。",
        "材料乙逐项否定材料甲列出的甘味常见作用。",
    ),
    (
        "辛味作用",
        "作用方向",
        "辛味和发散行气有什么联系？请整理成记忆卡。",
        "辛味常有发散、行气或行血的作用倾向。",
        "辛味只负责收敛固涩，发散、行气和行血都与辛味无关。",
        "材料乙将辛味的作用倾向解释为与材料甲相反的收敛固涩。",
    ),
    (
        "咸味作用",
        "作用方向",
        "咸味的软坚和泻下作用怎样理解？请列出学习关键词。",
        "咸味常有软坚散结、泻下等作用倾向。",
        "咸味只能固涩止泻，绝不能软坚散结或泻下。",
        "材料乙直接否定材料甲所述的咸味主要作用倾向。",
    ),
)

ADDITIONAL_BOUNDARIES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("肺主气与司呼吸", "总分关系", "肺主气和司呼吸怎样放在一个框架里理解？", "肺主气包括主持呼吸之气和参与一身之气的生成调节。", "肺司呼吸重点说明吸清呼浊，是肺主气在呼吸活动方面的具体表现。", "材料乙具体展开材料甲的一部分，没有否定总定义。"),
    ("脾运化与气血生化", "因果关系", "脾运化和气血生化之源怎样联系？", "脾运化水谷并吸收、转输水谷精微。", "水谷精微是化生气血的重要物质基础，因此脾被称为气血生化之源。", "两份材料构成前后相接的因果链。"),
    ("肝疏泄与胆汁", "总分关系", "肝疏泄和胆汁排泄怎样放在同一机制图里？", "肝主疏泄有助于全身气机调畅。", "胆汁的分泌排泄有赖于肝气疏泄，是气机调畅在消化方面的表现。", "材料乙是材料甲总体功能的具体表现。"),
    ("肾藏精与生长发育", "因果关系", "肾藏精为什么会和生长发育联系起来？", "肾藏先天之精并受后天之精充养。", "肾精可化生肾气，推动人体生长、发育和生殖。", "两份材料分别描述物质基础和功能结果。"),
    ("经络与腧穴", "载体关系", "经络和腧穴的关系怎样理解？", "经络是运行气血、联络脏腑肢节的网络。", "腧穴是脏腑经络之气输注于体表的特定部位，可依经络联系理解其作用。", "材料乙在材料甲网络框架中说明体表节点。"),
    ("望舌质与望舌苔", "并列分项", "舌诊为什么要同时看舌质和舌苔？", "舌质观察颜色、形态和动态等方面。", "舌苔观察苔色、厚薄、润燥等方面，两者合参才能形成较完整判断。", "两份材料是舌诊中可同时成立的并列观察维度。"),
    ("脉位与脉力", "并列维度", "判断脉象时脉位和脉力怎样一起记录？", "脉位可从浮沉等取脉层次描述。", "脉力可从有力无力描述，与脉位属于不同维度，应综合记录。", "两份材料描述不同且可组合的脉象维度。"),
    ("四气与五味", "并列属性", "四气和五味怎样共同描述药性？", "四气主要概括药物寒热温凉的性质倾向。", "五味主要概括酸苦甘辛咸及相关作用倾向，可与四气合并描述一味药。", "两份材料说明药性的不同并列维度。"),
    ("升降浮沉与归经", "并列属性", "升降浮沉和归经分别回答药物作用的什么问题？", "升降浮沉描述药物作用趋向。", "归经描述药物作用对脏腑经络的选择性，两者可同时标注。", "两份材料回答方向和部位两个不同问题。"),
    ("君药与佐药", "不同角色", "君药和佐药怎样在同一方剂里协作？", "君药针对主病或主证发挥主要治疗作用。", "佐药可协助治疗、制约毒性烈性或产生反佐作用，不会因此取消君药的主要地位。", "两份材料描述同一方剂中的不同协作角色。"),
    ("治法与方剂", "抽象实现", "治法和方剂是什么关系？", "治法是在辨证基础上确定的治疗原则和方法。", "方剂按治法组织药物配伍，是落实治法的一种具体形式。", "两份材料分别说明抽象原则和具体实现。"),
    ("八纲与脏腑辨证", "层次关系", "八纲辨证和脏腑辨证能否结合使用？", "八纲从表里、寒热、虚实、阴阳概括证候基本性质。", "脏腑辨证进一步定位相关脏腑及其功能失调，可与八纲性质判断结合。", "两份材料处于不同辨证层次并可联合使用。"),
)

PROMPT_WRAPPERS = (
    "我把两份课堂摘录放在一起了。{prompt}请整理成一份可直接复习的结论。",
    "{prompt}请综合材料甲和材料乙，给出简明的复习答案。",
    "根据下面两份同主题学习材料，{prompt}最后归纳为一段统一表述。",
    "我准备制作复习卡。{prompt}请在读完两份材料后完成整理。",
    "请先阅读两份资料再回答：{prompt}答案要便于闭卷复述。",
    "下面是同一主题的两段记录。{prompt}请归纳为考试复习要点。",
    "我想核对两份学习记录。{prompt}请给出清楚、可直接使用的整理结果。",
    "{prompt}请同时使用材料甲和材料乙，不补充材料外的裁决依据。",
)


def _strip_source_label(text: str) -> str:
    return re.sub(r"^《[^》]+》：", "", str(text)).strip()


def _target_pool() -> list[dict[str, str]]:
    original = [
        item
        for item in TARGETS
        if item.get("relation", "contradiction") == "contradiction"
    ]
    added = [
        {
            "topic": topic,
            "dimension": dimension,
            "prompt": prompt,
            "a": a,
            "b": b,
            "rationale": rationale,
        }
        for topic, dimension, prompt, a, b, rationale in ADDITIONAL_TARGETS
    ]
    authored_inventory = original + added
    if len(authored_inventory) < 76:
        raise ValueError(
            f"advantage target inventory must contain at least 76 cases, got "
            f"{len(authored_inventory)}"
        )
    # The stage budget was fixed at 8 + 8 + 60 before execution. Selection is
    # deterministic source order, never an observed model or judge outcome.
    items = authored_inventory[:76]
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(items):
        wrapper = PROMPT_WRAPPERS[index % len(PROMPT_WRAPPERS)]
        normalized.append(
            {
                **item,
                "prompt": wrapper.format(prompt=str(item["prompt"]).strip()),
                "a": "学习材料甲：" + _strip_source_label(item["a"]),
                "b": "学习材料乙：" + _strip_source_label(item["b"]),
                "relation": "contradiction",
                "selection_basis": "direct_logical_incompatibility_without_authority_metadata",
            }
        )
    return normalized


def _boundary_pool() -> list[dict[str, str]]:
    original = [item for item in TARGETS if item.get("relation") == "compatible"]
    added = [
        {
            "topic": topic,
            "dimension": dimension,
            "prompt": prompt,
            "a": a,
            "b": b,
            "rationale": rationale,
            "relation": "compatible",
        }
        for topic, dimension, prompt, a, b, rationale in ADDITIONAL_BOUNDARIES
    ]
    authored_inventory = original + added
    if len(authored_inventory) < 20:
        raise ValueError(
            f"advantage boundary inventory must contain at least 20 cases, got "
            f"{len(authored_inventory)}"
        )
    items = authored_inventory[:20]
    return [
        {
            **item,
            "prompt": PROMPT_WRAPPERS[(index + 3) % len(PROMPT_WRAPPERS)].format(
                prompt=str(item["prompt"]).strip()
            ),
            "a": "学习材料甲：" + _strip_source_label(item["a"]),
            "b": "学习材料乙：" + _strip_source_label(item["b"]),
            "selection_basis": "same_topic_compatible_claims_boundary_control",
        }
        for index, item in enumerate(items)
    ]


def _runtime_and_gold(
    *,
    stage: str,
    group_code: str,
    number: int,
    item: dict[str, str],
    case_group: str,
    task_type: str,
    pair_order: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = f"D1V5-{stage.upper()}-{group_code}{number:03d}"
    runtime: dict[str, Any] = {
        "schema_version": "d1-three-stage-runtime-1.1",
        "case_id": case_id,
        "stage": "final_ab" if stage == "final" else stage,
        "case_group": case_group,
        "prompt": item["prompt"],
        "task_type": task_type,
        "materials": [
            {"material_id": f"{case_id}-M1", "text": item["a"]},
            {"material_id": f"{case_id}-M2", "text": item["b"]},
        ],
        "formal_environment_write_allowed": False,
    }
    if pair_order is not None:
        runtime["pair_order"] = pair_order
    relation = (
        "not_applicable"
        if case_group == "ordinary_control"
        else item.get("relation", "contradiction")
    )
    gold = {
        "schema_version": "d1-three-stage-gold-1.1",
        "case_id": case_id,
        "stage": runtime["stage"],
        "gold_relation": relation,
        "relation_dimension": item.get("dimension", "ordinary_learning_support"),
        "gold_rationale": item["rationale"],
        "selection_basis": item["selection_basis"],
        "source_status": "synthetic_evaluation_fixture_pending_independent_human_review",
    }
    return runtime, gold


def build() -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    target_pool = _target_pool()
    boundaries = _boundary_pool()
    discovery_items = target_pool[:DISCOVERY_TARGET_COUNT]
    qualification_items = target_pool[
        DISCOVERY_TARGET_COUNT : DISCOVERY_TARGET_COUNT + QUALIFICATION_TARGET_COUNT
    ]
    final_targets = target_pool[
        DISCOVERY_TARGET_COUNT
        + QUALIFICATION_TARGET_COUNT : DISCOVERY_TARGET_COUNT
        + QUALIFICATION_TARGET_COUNT
        + FINAL_TARGET_COUNT
    ]
    stages: dict[str, list[dict[str, Any]]] = {
        "discovery": [],
        "qualification": [],
        "final_ab": [],
    }
    gold: list[dict[str, Any]] = []

    for stage, items in (
        ("discovery", discovery_items),
        ("qualification", qualification_items),
    ):
        for index, item in enumerate(items, start=1):
            runtime, annotation = _runtime_and_gold(
                stage=stage,
                group_code="T",
                number=index,
                item=item,
                case_group="target_fault",
                task_type="knowledge_explanation",
                pair_order=None,
            )
            stages[stage].append(runtime)
            gold.append(annotation)

    for index, item in enumerate(final_targets, start=1):
        runtime, annotation = _runtime_and_gold(
            stage="final",
            group_code="T",
            number=index,
            item=item,
            case_group="target_fault",
            task_type="knowledge_explanation",
            pair_order="AB" if index % 2 else "BA",
        )
        stages["final_ab"].append(runtime)
        gold.append(annotation)

    for index, item in enumerate(boundaries, start=1):
        runtime, annotation = _runtime_and_gold(
            stage="final",
            group_code="B",
            number=index,
            item=item,
            case_group="compatible_boundary",
            task_type="knowledge_explanation",
            pair_order="AB" if index % 2 else "BA",
        )
        stages["final_ab"].append(runtime)
        gold.append(annotation)

    for index, source in enumerate(NORMALS, start=1):
        item = {
            **source,
            "dimension": "ordinary_learning_support",
            "rationale": "两条材料提供可共同采用的学习建议，不涉及知识主张冲突。",
            "selection_basis": "ordinary_task_outside_candidate_task_type",
        }
        runtime, annotation = _runtime_and_gold(
            stage="final",
            group_code="N",
            number=index,
            item=item,
            case_group="ordinary_control",
            task_type="general_learning_support",
            pair_order="AB" if index % 2 else "BA",
        )
        stages["final_ab"].append(runtime)
        gold.append(annotation)
    return stages, gold


def _normalized(value: str) -> str:
    return "".join(str(value).split())


def validate(
    stages: dict[str, list[dict[str, Any]]],
    gold: list[dict[str, Any]],
) -> None:
    counts = {name: len(rows) for name, rows in stages.items()}
    if counts != {"discovery": 8, "qualification": 8, "final_ab": 100}:
        raise ValueError(f"invalid stage counts: {counts}")
    runtime = [row for rows in stages.values() for row in rows]
    ids = [row["case_id"] for row in runtime]
    if len(ids) != len(set(ids)) or {row["case_id"] for row in gold} != set(ids):
        raise ValueError("case identity or Gold coverage is invalid")
    prompts = [_normalized(row["prompt"]) for row in runtime]
    pairs = [
        tuple(_normalized(material["text"]) for material in row["materials"])
        for row in runtime
    ]
    if len(prompts) != len(set(prompts)) or len(pairs) != len(set(pairs)):
        raise ValueError("cross-stage prompt or material-pair overlap detected")
    forbidden = {
        "gold_relation",
        "gold_rationale",
        "expected_rule_exposure",
        "target_rule",
        "conflict_binding",
        "repair_instruction",
        "selection_basis",
    }
    for row in runtime:
        if forbidden.intersection(row):
            raise ValueError(f"runtime row leaks evaluator fields: {row['case_id']}")
        if row["formal_environment_write_allowed"] is not False:
            raise ValueError("formal writeback must remain disabled")
    groups = Counter(row["case_group"] for row in stages["final_ab"])
    orders = Counter(row["pair_order"] for row in stages["final_ab"])
    if groups != Counter(
        {"target_fault": 60, "compatible_boundary": 20, "ordinary_control": 20}
    ):
        raise ValueError(f"invalid final groups: {groups}")
    if orders != Counter({"AB": 50, "BA": 50}):
        raise ValueError(f"invalid pair order: {orders}")
    relations = Counter(row["gold_relation"] for row in gold)
    if relations != Counter(
        {"contradiction": 76, "compatible": 20, "not_applicable": 20}
    ):
        raise ValueError(f"invalid Gold distribution: {relations}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(
    stages: dict[str, list[dict[str, Any]]],
    gold: list[dict[str, Any]],
) -> None:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    stage_paths = {
        "discovery": DISCOVERY_DATASET,
        "qualification": QUALIFICATION_DATASET,
        "final_ab": FINAL_DATASET,
    }
    for stage, path in stage_paths.items():
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in stages[stage]
            ),
            encoding="utf-8",
        )
    GOLD_DATASET.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in gold),
        encoding="utf-8",
    )
    paths = [*stage_paths.values(), GOLD_DATASET]
    manifest = {
        "schema_version": "d1-three-stage-manifest-1.1",
        "dataset_id": DATASET_ID,
        "version": DATASET_VERSION,
        "supersedes_for_new_runs": "d1_three_stage_v5@5.0.0",
        "historical_v5_files_modified": False,
        "target_rule_family": TARGET_RULE_FAMILY,
        "pre_registered_target_population": (
            "learner-visible synthesis of two same-topic materials containing direct "
            "logical incompatibility, with no authority/version metadata that permits "
            "selecting one claim as correct"
        ),
        "enrichment_rationale": (
            "discovery identified unsupported conflict resolution; final targets are "
            "mechanism-proximal cases where the model-authored strategy can act directly"
        ),
        "effect_claim_boundary": (
            "enriched direct-conflict synthesis population only; no claim of average "
            "improvement across all platform tasks"
        ),
        "case_selection_completed_before_execution": True,
        "authored_target_inventory_count": 80,
        "selected_target_budget_count": 76,
        "target_selection_method": "first_76_in_frozen_source_order_before_execution",
        "authored_boundary_inventory_count": 21,
        "selected_boundary_budget_count": 20,
        "boundary_selection_method": "first_20_in_frozen_source_order_before_execution",
        "result_based_case_removal_allowed": False,
        "qualification_observed_effect_used_for_selection": False,
        "runtime_gold_separated": True,
        "judge_independence": False,
        "gold_independently_human_reviewed": False,
        "annotation_status": (
            "synthetic_evaluation_fixture_pending_independent_human_review"
        ),
        "stages": {
            "discovery": {
                "case_count": 8,
                "use": "baseline-only candidate discovery; excluded from effect estimates",
            },
            "qualification": {
                "case_count": 8,
                "use": "technical, isolation and measurement gate only; excluded from effect estimates",
            },
            "final_ab": {
                "case_count": 100,
                "paired_generation_count": 200,
                "groups": {
                    "target_fault": 60,
                    "compatible_boundary": 20,
                    "ordinary_control": 20,
                },
                "pair_orders": {"AB": 50, "BA": 50},
                "primary_endpoint": "learner_visible_semantic_net_rescue_rate",
            },
        },
        "formal_environment_write_allowed": False,
        "production_registration_allowed": False,
        "production_activation_allowed": False,
        "cross_stage_prompt_and_material_overlap": 0,
        "prompt_rendering_style_count": len(PROMPT_WRAPPERS),
        "files": {path.name: _sha256(path) for path in paths},
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    stages, gold = build()
    validate(stages, gold)
    write(stages, gold)
    print(
        json.dumps(
            {
                "valid": True,
                "dataset_id": DATASET_ID,
                "version": DATASET_VERSION,
                "stage_counts": {name: len(rows) for name, rows in stages.items()},
                "manifest": str(MANIFEST),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
