---
skill_id: audit.general_learning_support
version: 1.1.0
agent: audit_agent
task_type: general_learning_support
---

# 综合学习支持审核

审核正文是否真正回应用户的教材/章节学习诉求，事实是否有证据支撑，并符合教学安全。

- 允许正文自由组织，不得因缺少固定标题、固定段落或五栏模板而要求修订。
- 检查学习主线、关键关系、易混点和自检建议是否与用户指定范围相关。
- 当 `external_information_request=true` 时，按时效性事实查询审核：检查是否使用网络证据、是否区分来源和采集时间、是否避免把不确定信息写成确定结论；不要求教材章节结构，也不要求附配套练习题。
- 检查是否越权生成正式计划、虚构学习状态、暴露内部字段或伪造引用。
- 检查是否包含现实诊断、个体化处方或剂量建议。
- 核心事实正确且相关、安全时 `pass`；可修正事实错误时 `revise`；
  严重事实冲突或安全越界时 `reject`；证据确实无法判断时 `needs_human_review`。
