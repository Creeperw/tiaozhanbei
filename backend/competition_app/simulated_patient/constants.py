"""
常量配置
"""

# 患者性格风格
PATIENT_STYLES = [
    {"id": "gentle", "name": "温和配合型", "traits": "礼貌、配合、语速平缓",
     "emotional_tendency": "稳定型", "communication_style": "详细描述症状，主动配合",
     "opening_templates": ["大夫您好，我最近总觉得不太舒服，想请您帮忙看看。"]},
    {"id": "anxious", "name": "焦虑紧张型", "traits": "担忧、紧张、易焦虑",
     "emotional_tendency": "焦虑型", "communication_style": "反复询问严重程度，过度担忧",
     "opening_templates": ["医生，我是不是得了什么严重的病？您快帮我看看，我很害怕！"]},
    {"id": "depressive", "name": "抑郁低落型", "traits": "低落、无力感、话少",
     "emotional_tendency": "抑郁型", "communication_style": "话少，被动回答，语气低沉",
     "opening_templates": ["医生……我来看看……不知道能不能治好……"]},
    {"id": "irritable", "name": "急躁易怒型", "traits": "急躁、易怒、没有耐心",
     "emotional_tendency": "易怒型", "communication_style": "语速快，容易打断医生",
     "opening_templates": ["医生您快点给我看看！这病拖了我太久了！"]},
    {"id": "introverted", "name": "内向寡言型", "traits": "害羞、被动、不敢主动表达",
     "emotional_tendency": "退缩型", "communication_style": "话很少，需要引导",
     "opening_templates": ["医生好……我，我有点不舒服……不知道怎么说……"]},
    {"id": "talkative", "name": "话多发散型", "traits": "健谈、发散、容易跑题",
     "emotional_tendency": "兴奋型", "communication_style": "话多但容易偏离主题",
     "opening_templates": ["医生您好！我跟您说啊，我这毛病可久了……"]},
    {"id": "suspicious", "name": "多疑不信任型", "traits": "多疑、不信任医生",
     "emotional_tendency": "多疑型", "communication_style": "反复确认诊断，质疑方案",
     "opening_templates": ["医生，您能保证诊断准确吗？我上次就被误诊了。"]},
    {"id": "optimistic", "name": "乐观开朗型", "traits": "积极、乐观、配合度高",
     "emotional_tendency": "乐观型", "communication_style": "积极回应，相信医生",
     "opening_templates": ["医生您好！我最近身体有点小问题，相信您一定能帮我治好！"]}
]

# 评分配置
SCORE_CONFIG = {
    "full": {
        "syndrome_max": 40,
        "prescription_name_max": 15,
        "prescription_comp_max": 15,
        "inquiry_max": 15,
        "time_efficiency_max": 8,
        "compassion_max": 7
    }
}

# 帮助功能触发轮次
HELP_AFTER_TURNS = 10

# 会话超时时间（秒）
SESSION_TTL = 3600

# LLM 默认参数
DEFAULT_LLM_TEMPERATURE = 0.7
DEFAULT_LLM_MAX_TOKENS = 500

# 评分 LLM 参数
GRADING_LLM_TEMPERATURE = 0.2
GRADING_LLM_MAX_TOKENS = 2500