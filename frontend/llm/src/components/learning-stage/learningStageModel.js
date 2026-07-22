export const STAGE_PALETTE = [
  ['#3F8F68', '#2E7150'],
  ['#347D70', '#285F57'],
  ['#33777B', '#285C63'],
  ['#3B6876', '#2D505E'],
  ['#3B586A', '#2D4353'],
  ['#293D4C', '#1D2C38'],
];

export const DEFAULT_LEARNING_STAGES = Object.freeze([
  {
    id: 'foundation', level: '入门', title: '基础筑基', duration: '1-2个月',
    tasks: ['阴阳五行藏象学说', '望闻问切四诊基础'], resources: ['基础导学'],
    illustration: '/learning-stage/foundation.png', illustrationPosition: '68% 72%',
  },
  {
    id: 'classics', level: '基础', title: '经典研读', duration: '2-3个月',
    tasks: ['《伤寒论》六经辨证', '《金匮要略》杂病论治', '《温病条辨》温病学', '背诵常用方剂歌诀'],
    resources: ['经典原文', '名家讲解'],
    illustration: '/learning-stage/classics.png', illustrationPosition: '38% 76%',
  },
  {
    id: 'formulas', level: '提高', title: '中药方剂', duration: '2-3个月',
    tasks: ['300+常用中药药性', '中药炮制方法功效', '100+经典方剂组成', '君臣佐使配伍原则'],
    resources: ['药性歌诀', '方剂手册'],
    illustration: '/learning-stage/formulas.png', illustrationPosition: '64% 72%',
  },
  {
    id: 'clinical', level: '进阶', title: '临床实践', duration: '3-6个月',
    tasks: ['跟师门诊观察学习', '针灸推拿特色疗法', '参与病例讨论分析', '练习脉诊舌诊技能'],
    resources: ['名医跟诊', '针灸培训'],
    illustration: '/learning-stage/clinical.png', illustrationPosition: '60% 68%',
  },
  {
    id: 'specialty', level: '专精', title: '专科深化', duration: '3-6个月',
    tasks: ['选择内科/妇科/儿科', '学习专科经典著作', '研究专科名医经验', '收集分析专科医案'],
    resources: ['专科专著', '经验集'],
    illustration: '/learning-stage/specialty.png', illustrationPosition: '60% 66%',
  },
  {
    id: 'mastery', level: '精通', title: '融会贯通', duration: '持续精进',
    tasks: ['独立接诊积累医案', '参加学术交流研讨', '研究现代中医成果', '总结个人诊疗心得'],
    resources: ['学术会议', '科研论文'],
    illustration: '/learning-stage/mastery.png', illustrationPosition: '82% 28%',
  },
]);

export const STAGE_FLIP_DURATION_MS = 640;

export function getStageLayout(count) {
  const safeCount = Math.max(1, Number(count) || 1);
  return Array.from({ length: safeCount }, (_, index) => ({
    progress: safeCount === 1 ? 1 : index / (safeCount - 1),
  }));
}
