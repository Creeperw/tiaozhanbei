"""题型词表的唯一来源。

组卷链路上每个环节都要回答「这道题属于哪种题型」，而题型值来自三个互不
相干的地方：公共题库（中文规范名）、网络题抽取器（模型自由输出的中英文
混排）、考试约束（平台协议的英文枚举）。此前每个环节各写一张别名表，五
张表的覆盖范围互不一致，线上已经因此出过两次事故：

* 网络题被写成英文 ``single_choice``，而候选准入的别名表只认中文，题型
  过滤把它当成未知题型静默丢弃——检索到并已入库的网络题一道都进不了候选
  池，卷面只能靠现场生成补齐。
* 组卷按一张表把「临床案例问答」计入简答题配额，卷面却按另一张表写原名，
  审核读到卷面标注与蓝图要求不符，整卷返修一轮后仍不发布。

因此这里集中定义词表与归一化，其他环节一律调用本模块，不再各写一张。

归一化只做**词汇翻译**，不做**语义折叠**：无法识别的值原样返回，由调用方
决定是丢弃还是如实上报。这样词表缺什么会立刻暴露在计数里，而不是像静默
替换那样把「不认识」伪装成「已处理」。
"""

from __future__ import annotations

from typing import Any

# 规范名。取自公共题库实际使用的题型名，不新造同义词。
SINGLE_CHOICE = "单项选择题"
MULTIPLE_CHOICE = "多项选择题"
TRUE_FALSE = "判断题"
FILL_BLANK = "填空题"
SHORT_ANSWER = "简答题"

# 客观题：选项与标准答案必须自洽，交付前要逐题校验。
CHOICE_QUESTION_TYPES = frozenset(
    {SINGLE_CHOICE, MULTIPLE_CHOICE, "选择题"}
)

# 词表全部以小写、无空格、无下划线的形式作键，取值时同样处理，这样
# ``Single_Choice``、``single choice`` 与 ``single_choice`` 是同一条。
_ALIASES: dict[str, str] = {
    # 平台协议的英文枚举。网络题抽取器的提示词只声明字段名、不声明允许
    # 值，模型会把平台枚举原样写回来，这里必须认。
    "singlechoice": SINGLE_CHOICE,
    "multiplechoice": MULTIPLE_CHOICE,
    "truefalse": TRUE_FALSE,
    "judge": TRUE_FALSE,
    "fillblank": FILL_BLANK,
    "shortanswer": SHORT_ANSWER,
    # 案例题在题库里按简答题计配额、按简答题写卷面，这里统一到同一个名字，
    # 避免同一个知识点下同时存在两种案例题型写法。
    "casequiz": SHORT_ANSWER,
    "caseanalysis": SHORT_ANSWER,
    "case": SHORT_ANSWER,
    # 中文同义与题库历史写法。
    "单选题": SINGLE_CHOICE,
    "单项选择": SINGLE_CHOICE,
    "多选题": MULTIPLE_CHOICE,
    "多项选择": MULTIPLE_CHOICE,
    "选择题": "选择题",
    "简答": SHORT_ANSWER,
    "问答": SHORT_ANSWER,
    "问答题": SHORT_ANSWER,
    "临床案例问答": SHORT_ANSWER,
    "案例分析": SHORT_ANSWER,
    "案例分析题": SHORT_ANSWER,
    "病例分析": SHORT_ANSWER,
    "病例分析题": SHORT_ANSWER,
    "病例分析/实践技能": SHORT_ANSWER,
}

# 归一化结果里属于「已识别」的规范名。调用方用它区分「词表认识这个题型」
# 与「原样透传的未知写法」。
KNOWN_QUESTION_TYPES = frozenset(
    {SINGLE_CHOICE, MULTIPLE_CHOICE, TRUE_FALSE, FILL_BLANK, SHORT_ANSWER, "选择题"}
)


def _alias_key(value: str) -> str:
    return value.strip().replace(" ", "").replace("_", "").lower()


def normalize_question_type(value: Any) -> str:
    """把题型写法归一到规范名；词表不认识的值原样返回。

    原样返回是刻意的：调用方需要能分辨「这是简答题」和「这是我不认识的
    写法」。若在此处兜底成某个默认题型，未知题型会被伪装成已识别题型，
    既可能被误放进卷面，也会让词表缺口永远不被发现。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    return _ALIASES.get(_alias_key(text), text)


def is_known_question_type(value: Any) -> bool:
    """归一化后是否落在已知规范名内。"""

    return normalize_question_type(value) in KNOWN_QUESTION_TYPES
