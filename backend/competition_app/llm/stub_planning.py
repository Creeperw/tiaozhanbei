"""Offline fixtures only: stable prose and literal extraction, never Live semantics."""

import re


def fixed_route_document(route, goal="当前考试"):
    stages = list(route.get("stages") or [])
    if not stages:
        return ""
    sections = [f"【最终目标】围绕{goal}按规范路线完成教材学习，以闭卷回忆与辨析记录验收。", "【能力路径与阶段】"]
    for stage in stages:
        books = "；".join(stage.get("books") or [])
        sections.extend([
            f"### {stage['stage_id']} {stage.get('name', '')}",
            f"教材：{books}",
            f"目标：{stage.get('goal') or stage.get('objective') or '完成阶段目标'}",
            "阶段天数：30天",
            f"安排：使用{books}完成阅读、闭卷回忆和辨析练习，提交纠错记录，以{'；'.join(stage.get('exit_evidence') or ['准确解释核心内容'])}验收。",
        ])
    for rule in route.get("prerequisites") or []:
        sections.append(f"前置训练：进入依赖教材前先安排《{rule['course']}》诊断与基础训练，纳入阶段30天内，完成证候辨析并通过测评后再推进。")
    sections.extend([
        "【阶段里程碑】各阶段提交回忆与纠错记录，测评通过后晋级。",
        "【资源预算】按用户已登记的每日时间安排，保留反馈与机动时间。",
        "【重规划条件】连续未通过验收或可用时间持续变化时调整。",
        "【保温底线】每周至少回顾一次知识卡片。",
        f"当前阶段：{stages[0]['stage_id']}",
        f"当前教材：{'；'.join(list(stages[0].get('books') or [])[:1])}",
        "当前用途：diagnostic",
        "选择依据：先诊断基础，不把课程背景当作整本完成或能力通过。",
    ])
    return "\n".join(sections)


def extract_fixed_route(document):
    stages, anchors = [], {}
    for index, match in enumerate(re.finditer(
        r"(?m)^### (\S+) ([^\n]*)\n教材：([^\n]*)\n目标：([^\n]*)\n阶段天数：(\d+)天\n安排：([^\n]+)", document
    )):
        stage_id, _, _, _, days, summary = match.groups()
        stages.append({"stage_id": stage_id, "duration_days": int(days), "schedule_summary": summary, "acceptance": []})
        for field, quote in (("stage_id", match.group(0)), ("duration_days", f"阶段天数：{days}天"), ("schedule_summary", summary)):
            anchors[f"/stages/{index}/{field}"] = [{"source_field": "plan_document", "source_quote": quote}]
    values = {}
    for key, label in (("selected_stage_id", "当前阶段"), ("selected_books", "当前教材"), ("selection_mode", "当前用途"), ("selection_reason", "选择依据")):
        match = re.search(rf"(?m)^{label}：([^\n]+)$", document)
        if match is None:
            return {"status": "needs_revision", "issues": [{"code": "missing_required_field", "category": "missing", "field_path": f"/{key}"}]}
        text = match.group(1)
        values[key] = text.split("；") if key == "selected_books" else text
        anchors[f"/{key}"] = [{"source_field": "plan_document", "source_quote": text}]
    return {"status": "compiled", "contract": {"scope": "long_term", "stages": stages, "field_anchors": anchors, **values}}