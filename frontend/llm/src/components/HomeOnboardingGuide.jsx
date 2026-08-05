import React, { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Bot,
  BrainCircuit,
  CheckCircle2,
  ClipboardCheck,
  Database,
  GraduationCap,
  HeartPulse,
  Map,
  MessageCircle,
  MousePointer2,
  Network,
  Pause,
  Plus,
  RotateCcw,
  Target,
  Upload,
  UserRound,
} from 'lucide-react';
import LearningPathOverview from './learning-tree/LearningPathOverview';
import LearningStageLanding from './learning-stage/LearningStageLanding';
import { DEFAULT_LEARNING_STAGES } from './learning-stage/learningStageModel';
import LearningActivityHeatmap from './LearningActivityHeatmap';
import LearningTrendDualAxisChart from './LearningTrendDualAxisChart';
import { RadarChart } from './ReportsPage';
import SimulatedPatientChat from './SimulatedPatientChat';
import SmartPaperPanel from './SmartPaperPanel';
import TextbookLibrary from './workshop-textbook/TextbookLibrary';
import './HomeOnboardingGuide.css';

const SOURCE_DURATION = Math.round(1920 * 0.7);
const DESTINATION_DURATION = 2520;

const TOP_MODULES = [
  { icon: Map, label: '平台首页', meta: '查看平台概览与快捷入口' },
  { icon: Target, label: '学习目标', meta: '选择资格考试与学习方向' },
  { icon: BookOpen, label: '学习路径', meta: '查看个性化阶段学习路线' },
  { icon: Bot, label: '学习工坊', meta: '使用助教、图谱和知识库' },
  { icon: ClipboardCheck, label: '练习工坊', meta: '完成专项题与模拟练习' },
  { icon: BarChart3, label: '个性数据', meta: '查看学习趋势与成长记录' },
];

const CORE_CAPABILITIES = [
  { icon: BrainCircuit, label: '多智能体协同', meta: '六类智能体协作完成学习任务' },
  { icon: Map, label: '个性化学习路径', meta: '根据目标和表现动态规划' },
  { icon: Network, label: '知识图谱驱动', meta: '建立知识点之间的关联' },
  { icon: BarChart3, label: '数据驱动成长', meta: '用学习数据持续优化策略' },
];

const CERTIFICATES = [
  { icon: Target, label: '中医执业医师资格考试', meta: '完整执业医师考试路线', selected: true },
  { icon: Target, label: '中医执业助理医师资格考试', meta: '助理医师阶段考试路线' },
  { icon: GraduationCap, label: '中西医结合执业医师资格考试', meta: '覆盖中西医综合科目' },
  { icon: GraduationCap, label: '中西医结合执业助理医师资格考试', meta: '中西医结合助理方向' },
  { icon: BookOpen, label: '执业药师职业资格考试（中药学类）', meta: '聚焦中药学专业知识' },
];

/* Legacy scenes retained below only as source material; GUIDE_STEPS is the active flow. */
const LEGACY_GUIDE_STEPS = [
  {
    key: 'home', label: '平台首页', className: 'home-guide-target--home', pointer: { x: '34.38%', y: '4.87%' },
    scenes: [
      { title: '定位平台首页', description: '点击顶部“平台首页”，可以随时从其他功能返回统一的学习入口。', details: ['快速回到学习工作台', '重新查看当前任务与平台能力'], view: 'source' },
      { title: '认识六大顶部模块', description: '顶部导航集中连接平台首页、学习目标、学习路径、学习工坊、练习工坊和个性数据。', details: ['六个模块覆盖完整学习流程', '在任意页面都能快速切换'], view: 'home-nav' },
      { title: '认识四大核心功能', description: '首页下方展示多智能体协同、个性化学习路径、知识图谱驱动和数据驱动成长。', details: ['点击卡片查看能力详情', '四项能力共同形成学习闭环'], view: 'home-capabilities' },
    ],
  },
  {
    key: 'target', label: '学习目标', className: 'home-guide-target--learning-target', pointer: { x: '40.19%', y: '4.87%' },
    scenes: [
      { title: '打开学习目标', description: '点击“学习目标”展开资格考试与专业方向选择。', details: ['可随时查看当前目标', '支持切换不同考试方向'], view: 'source' },
      { title: '选择资格证书', description: '右侧提供五类资格证书，选择后平台会据此调整知识范围、任务难度和训练内容。', details: ['匹配对应考试大纲与科目', '学习路径与训练同步更新'], view: 'certificates' },
    ],
  },
  {
    key: 'path', label: '学习路径', className: 'home-guide-target--learning-path', pointer: { x: '46.67%', y: '4.87%' },
    scenes: [
      { title: '进入学习路径', description: '点击“学习路径”查看系统为当前目标生成的阶段路线。', details: ['按阶段组织学习内容', '显示已完成与待完成任务'], view: 'source' },
      { title: '查看长期学习规划', description: '长期规划按照基础筑基、经典研读、中药方剂、临床实践等阶段逐级展开，帮助你把握完整进阶方向。', details: ['动画鼠标选择长期阶段', '每个阶段包含周期、任务与推荐资源'], view: 'long-path' },
      { title: '查看短期学习路径', description: '系统把近期教材与知识任务组织成环形路径，清楚标记已完成、学习中、下一阶段和待解锁内容。', details: ['动画鼠标点击当前学习阶段', '掌握度与阶段状态实时更新'], view: 'short-path' },
    ],
  },
  {
    key: 'practice', label: '学习工坊', className: 'home-guide-target--practice', pointer: { x: '52.46%', y: '4.88%' },
    scenes: [
      { title: '打开学习工坊', description: '点击“学习工坊”进入智能助教、知识图谱和个人资料工具。', details: ['集中访问学习辅助工具', '自动携带当前学习上下文'], view: 'source' },
      { title: '在智能助教中新建对话', description: '进入智能助教后，点击左侧“新对话”即可创建独立会话，围绕新的学习问题重新开始。', details: ['动画演示点击并创建会话', '历史对话仍保留在左侧列表'], view: 'assistant-new-chat' },
      { title: '浏览教材学习内容', description: '教材学习区按长期计划汇总专业教材，向下滑动可以继续查看教材简介并进入章节学习。', details: ['动态演示教材列表向下滚动', '支持搜索教材和按章节继续学习'], view: 'textbook-scroll' },
    ],
  },
  {
    key: 'training', label: '练习工坊', className: 'home-guide-target--training', pointer: { x: '59.26%', y: '4.70%' },
    scenes: [
      { title: '打开练习工坊', description: '点击“练习工坊”选择题目练习、模拟患者、错题变式或试卷生成。', details: ['四种练习模式统一入口', '练习记录自动归入学习数据'], view: 'source' },
      { title: '模拟问诊演练', description: '进入系统真实的模拟问诊界面，医生会围绕主诉、病程和伴随症状连续提问，患者根据病例逐步作答。', details: ['动态演示一次连续问诊', '问诊内容与轮次同步更新'], view: 'consultation-live' },
      { title: '使用智能组卷', description: '进入系统真实的智能组卷界面，向下浏览出题范围、题型题量、作答模式和组卷预览。', details: ['动态向下浏览完整组卷配置', '支持按目标自动组织题目内容'], view: 'paper-scroll' },
    ],
  },
  {
    key: 'profile', label: '个性数据', className: 'home-guide-target--profile', pointer: { x: '65.63%', y: '5.04%' },
    scenes: [
      { title: '进入个性数据', description: '点击“个性数据”查看学习投入、任务完成和知识掌握情况。', details: ['汇总各模块学习记录', '按周观察学习节奏'], view: 'source' },
      { title: '查看真实学情图表', description: '学情报告通过能力雷达图、学习趋势和学习活跃度，从结构、变化与持续性三个角度呈现学习状态。', details: ['雷达图展示六项能力结构', '趋势与活跃度来自同一学习行为数据'], view: 'profile-insights' },
    ],
  },
];

const GUIDE_STEPS = [
  {
    key: 'target-overview', label: '学习目标', className: 'home-guide-target--learning-target', sourceTargetClass: 'home-guide-target--exam-category', pointer: { x: '34.2%', y: '4.2%' },
    title: '一、确定学习目标', description: '选择您的目标证书，以便为您制定可靠的全方位服务',
    scenes: [
      { title: '确定学习目标', description: '', details: [], view: 'source' },
      { title: '选择目标证书', description: '', details: [], view: 'certificates' },
    ],
  },
  {
    key: 'profile-overview', label: '学习画像', className: 'home-guide-target--learning-path', sourceTargetClass: 'home-guide-target--learning-path-nav', pointer: { x: '40.2%', y: '4.2%' },
    title: '二、了解当前学情', description: '向系统提供您的具体学习情况，系统将为您制定个性化的长短期学习路径规划和具有针对性的学习资源',
    scenes: [
      { title: '进入学习路径', description: '', details: [], view: 'source' },
      { title: '编辑学习画像', description: '', details: [], view: 'profile-edit' },
      { title: '查看长期学习规划', description: '', details: [], view: 'long-path' },
      { title: '查看短期学习路径', description: '', details: [], view: 'short-path' },
    ],
  },
  {
    key: 'study-overview', label: '学习工坊', className: 'home-guide-target--practice', sourceTargetClass: 'home-guide-target--teaching-resources', pointer: { x: '46.0%', y: '4.2%' },
    title: '三、开始中医学习', description: '根据系统提供的学习路径或者您的个性化需求，可以在海量学习教材中选择需要学习的内容，并且有相应视频辅助学习',
    scenes: [
      { title: '进入学习工坊', description: '', details: [], view: 'source' },
      { title: '浏览教材学习内容', description: '', details: [], view: 'textbook-scroll' },
    ],
  },
  {
    key: 'result-overview', label: '练习工坊', className: 'home-guide-target--training', sourceTargetClass: 'home-guide-target--training-nav', pointer: { x: '51.3%', y: '4.2%' },
    title: '四、检验学习成果', description: '练习工坊中不仅包含知识点专项练习、综合真题、错题集等基础功能，还有错题变式、智能组卷和AI模拟病患等创新性功能，辅助您全面掌握重难点模块',
    scenes: [
      { title: '进入练习工坊', description: '', details: [], view: 'source' },
      { title: '浏览综合套题与历年真题', description: '', details: [], view: 'comprehensive-paper' },
      { title: '模拟问诊演练', description: '', details: [], view: 'consultation-live' },
      { title: '使用智能组卷', description: '', details: [], view: 'paper-scroll' },
    ],
  },
  {
    key: 'problem-overview', label: '学习工坊', className: 'home-guide-target--practice', sourceTargetClass: 'home-guide-target--assistant-button', pointer: { x: '77.2%', y: '4.2%' },
    title: '五、解决学习问题', description: '基础知识点遗忘，难题解决不了，对于学习感到迷茫等，都可以找时诊智训助教，它将为您答疑解惑',
    scenes: [
      { title: '进入学习工坊', description: '', details: [], view: 'source' },
      { title: '与智能助教动态对话', description: '', details: [], view: 'assistant-new-chat', duration: DESTINATION_DURATION },
    ],
  },
  {
    key: 'data-overview', label: '个性数据', className: 'home-guide-target--profile', sourceTargetClass: 'home-guide-target--personal-data', pointer: { x: '57.6%', y: '4.2%' },
    title: '六、个性数据更新', description: '在学习、复习、做题等若干过程中，系统将实时保存您的学习数据，实时更新近期的学习建议，并在“个性数据”页面中为您直观展现',
    scenes: [
      { title: '进入个性数据', description: '', details: [], view: 'source' },
      { title: '查看真实学情图表', description: '', details: [], view: 'profile-insights' },
    ],
  },
];

const DESCRIPTION_HIGHLIGHTS = [
  ['目标证书'],
  ['具体学习情况', '个性化', '长短期学习路径规划', '学习资源'],
  ['学习路径', '个性化需求', '海量学习教材', '视频辅助学习'],
  ['练习工坊', '综合真题', '错题变式', '智能组卷', 'AI模拟病患'],
  ['基础知识点遗忘', '难题解决不了', '时诊智训助教'],
  ['实时保存', '实时更新', '“个性数据”'],
];

// The second phase points at the real navigation controls below the portal.
const SPOTLIGHT_TARGET_SELECTORS = {
  'target-overview': '.app-shell__target-group',
  'profile-overview': 'a[href="#learning-path"]',
  'study-overview': 'a[href="#practice"]',
  'result-overview': 'a[href="#training-workshop"]',
  'problem-overview': '.app-shell__assistant-entry--featured',
  'data-overview': 'a[href="#personalization"]',
};

const SPOTLIGHT_FALLBACKS = {
  // Include the current-exam label rendered beneath the category button.
  'target-overview': { left: 31.8, top: 0.7, width: 8.2, height: 11.8 },
  'profile-overview': { left: 39.3, top: 0.7, width: 6.2, height: 8.4 },
  'study-overview': { left: 44.4, top: 1.6, width: 5.4, height: 6.5 },
  'result-overview': { left: 50.4, top: 1.6, width: 5.2, height: 6.5 },
  'problem-overview': { left: 76.8, top: 0.7, width: 7, height: 8.4 },
  'data-overview': { left: 56.8, top: 0.7, width: 6.8, height: 8.4 },
};

function HighlightedDescription({ text, stepIndex }) {
  const highlights = DESCRIPTION_HIGHLIGHTS[stepIndex] || [];
  if (!highlights.length) return text;
  const ordered = [...highlights].sort((left, right) => right.length - left.length);
  const parts = [];
  let cursor = 0;
  while (cursor < text.length) {
    const match = ordered.find((item) => text.startsWith(item, cursor));
    if (match) {
      parts.push(<strong key={`${match}-${cursor}`} className="home-guide__highlight">{match}</strong>);
      cursor += match.length;
      continue;
    }
    const next = ordered.reduce((position, item) => {
      const found = text.indexOf(item, cursor + 1);
      return found >= 0 && found < position ? found : position;
    }, text.length);
    parts.push(text.slice(cursor, next));
    cursor = next;
  }
  return parts;
}

const PATH_ITEMS = [
  { icon: BookOpen, label: '基础巩固', meta: '已完成 · 100%', selected: true },
  { icon: Map, label: '方剂学专题', meta: '进行中 · 72%' },
  { icon: ClipboardCheck, label: '临床辨证实战', meta: '下一阶段' },
];

// Keep the legacy scene data available for reference while the six-step flow is used below.
void LEGACY_GUIDE_STEPS;

const PRACTICE_ITEMS = [
  { icon: Bot, label: '智能助教', meta: '即时答疑与追问', selected: true },
  { icon: Network, label: '知识图谱', meta: '查看知识关联' },
  { icon: Database, label: '个人知识库', meta: '沉淀笔记与资料' },
  { icon: Upload, label: '资料上传', meta: '建立个人学习资源' },
];

const TRAINING_ITEMS = [
  { icon: ClipboardCheck, label: '题目练习', meta: '按知识点专项练习', selected: true },
  { icon: UserRound, label: 'AI 病患模拟', meta: '练习临床问诊思路' },
  { icon: BookOpen, label: '错题变式', meta: '针对错误举一反三' },
  { icon: GraduationCap, label: '试卷生成', meta: '创建综合模拟试卷' },
];

const SHORT_PATH_NODES = [
  { membership_id: 'theory', title: '中医基础理论', status: 'completed', total_count: 18, order: 1 },
  { membership_id: 'diagnosis', title: '中医诊断学', status: 'completed', total_count: 14, order: 2 },
  { membership_id: 'materia', title: '中药学', status: 'in_progress', average_mastery: 72, total_count: 20, order: 3 },
  { membership_id: 'formulas', title: '方剂学', status: 'next', total_count: 16, order: 4 },
  { membership_id: 'classics', title: '经典研读', status: 'locked', total_count: 12, order: 5 },
  { membership_id: 'clinical', title: '临床实践', status: 'locked', total_count: 10, order: 6 },
];

const SHORT_PATH_EDGES = SHORT_PATH_NODES.slice(0, -1).map((node, index) => ({
  from: node.membership_id,
  to: SHORT_PATH_NODES[index + 1].membership_id,
  kind: 'sequence',
}));

const GUIDE_TEXTBOOKS = [
  { id: 'basic', book: '中医学基础', stage_title: '基础筑基' },
  { id: 'culture', book: '中医文化学', stage_title: '中医文化' },
  { id: 'formulas', book: '方剂学', stage_title: '中药方剂' },
  { id: 'acupuncture', book: '针灸学', stage_title: '临床实践' },
];

const GUIDE_DIMENSIONS = [
  { key: 'mastery', label: '知识掌握', value: 0.78 },
  { key: 'accuracy', label: '练习得分', value: 0.71 },
  { key: 'consistency', label: '学习规律', value: 0.62 },
  { key: 'retention', label: '复习保持', value: 0.68 },
  { key: 'execution', label: '任务执行', value: 0.84 },
  { key: 'engagement', label: '资源使用', value: 0.73 },
];

const GUIDE_TREND_SERIES = Array.from({ length: 84 }, (_, index) => {
  const date = new Date(Date.UTC(2026, 4, 1 + index));
  const active = index % 7 !== 1 && index % 11 !== 0;
  return {
    date: date.toISOString().slice(0, 10),
    focus_minutes: active ? 24 + ((index * 13) % 48) : 0,
    task_completion_rate: active ? 0.52 + (((index * 7) % 42) / 100) : 0,
    login_days: active ? 1 : 0,
  };
});

function DemoPageHeader({ title }) {
  return (
    <header className="home-guide__page-header">
      <span className="home-guide__brand"><GraduationCap size={17} aria-hidden="true" /><strong>时珍智训</strong></span>
      <strong>{title}</strong>
      <span className="home-guide__sync"><CheckCircle2 size={14} aria-hidden="true" /> 数据已同步</span>
    </header>
  );
}

function AnimatedDemoPointer({ className = '' }) {
  return (
    <span className={`home-guide__destination-pointer ${className}`} aria-hidden="true">
      <MousePointer2 size={22} fill="currentColor" />
    </span>
  );
}

function ShortPathDemo() {
  return (
    <div className="home-guide__real-page home-guide__real-path" data-testid="short-term-path-demo">
      <DemoPageHeader title="学习路径规划 · 短期" />
      <div className="home-guide__real-path-content">
        <LearningPathOverview
          nodes={SHORT_PATH_NODES}
          edges={SHORT_PATH_EDGES}
          selectedId="materia"
          onSelect={() => {}}
          onDrill={() => {}}
          directDrill
          summaryLabel="短期学习路径"
          homeCompact
        />
      </div>
      <AnimatedDemoPointer className="home-guide__destination-pointer--short-path" />
    </div>
  );
}

function LongPathDemo() {
  return (
    <div className="home-guide__real-page home-guide__long-path" data-testid="long-term-path-demo">
      <DemoPageHeader title="学习路径规划 · 长期" />
      <div className="home-guide__long-path-heading">
        <span>LONG-TERM LEARNING PLAN</span>
        <strong>长期学习路径规划图</strong>
        <small>从基础筑基到融会贯通，按阶段持续进阶</small>
      </div>
      <div className="home-guide__long-path-content">
        <LearningStageLanding
          compact
          stages={DEFAULT_LEARNING_STAGES}
          currentStageId="formulas"
          onStageSelect={() => {}}
        />
      </div>
      <AnimatedDemoPointer className="home-guide__destination-pointer--long-path" />
    </div>
  );
}

function AssistantNewChatDemo() {
  return (
    <div className="home-guide__assistant-demo home-guide__assistant-live-demo" data-testid="assistant-new-chat-demo">
      <aside className="home-guide__assistant-rail" aria-label={'\u6f14\u793a\u4f1a\u8bdd\u5217\u8868'}>
        <div className="home-guide__assistant-identity"><span><HeartPulse size={16} /></span><strong>{'\u65f6\u73cd\u667a\u8bad\u667a\u80fd\u52a9\u6559'}</strong></div>
        <button type="button" className="home-guide__assistant-new"><Plus size={14} />{'\u65b0\u5bf9\u8bdd'}</button>
        <small>{'\u5386\u53f2\u8bb0\u5f55'}</small>
        <div className="home-guide__assistant-session is-current"><MessageCircle size={13} /><span>{'\u65b9\u5242\u914d\u4f0d\u590d\u4e60'}</span></div>
        <div className="home-guide__assistant-session"><MessageCircle size={13} /><span>{'\u4e2d\u533b\u8bca\u65ad\u5b66\u4e60\u8ba1\u5212'}</span></div>
        <div className="home-guide__assistant-session"><MessageCircle size={13} /><span>{'\u4e34\u5e8a\u8fa8\u8bc1\u63d0\u95ee'}</span></div>
      </aside>
      <main className="home-guide__assistant-main">
        <header><span className="home-guide__assistant-status" /><strong>{'\u667a\u80fd\u52a9\u6559 \u00b7 \u65b9\u5242\u914d\u4f0d\u590d\u4e60'}</strong></header>
        <div className="home-guide__assistant-conversation-before home-guide__assistant-live-track">
          <div className="home-guide__assistant-bubble">{'\u6211\u603b\u662f\u8bb0\u4e0d\u4f4f\u65b9\u5242\u7684\u541b\u81e3\u4f50\u4f7f\uff0c\u5e94\u8be5\u600e\u6837\u590d\u4e60\uff1f'}</div>
          <div className="home-guide__assistant-bubble is-answer">{'\u5148\u4ece\u4e3b\u6cbb\u75c5\u673a\u5165\u624b\uff0c\u518d\u6309\u6838\u5fc3\u529f\u6548\u548c\u914d\u4f0d\u5173\u7cfb\u5efa\u7acb\u8bb0\u5fc6\u6846\u67b6\u3002'}</div>
          <div className="home-guide__assistant-bubble">{'\u80fd\u7ed3\u5408\u6211\u7684\u5b66\u4e60\u753b\u50cf\u7ed9\u4e00\u4e2a\u7ec3\u4e60\u65b9\u6cd5\u5417\uff1f'}</div>
          <div className="home-guide__assistant-bubble is-answer">{'\u53ef\u4ee5\u3002\u5148\u5b8c\u6210\u57fa\u7840\u8fa8\u6790\u9898\uff0c\u518d\u7528\u9519\u9898\u53d8\u5f0f\u5de9\u56fa\u8584\u5f31\u77e5\u8bc6\u70b9\uff0c\u6211\u4f1a\u6301\u7eed\u8bb0\u5f55\u4f60\u7684\u638c\u63e1\u53d8\u5316\u3002'}</div>
          <div className="home-guide__assistant-bubble">{'\u5982\u679c\u9047\u5230\u96be\u9898\uff0c\u6211\u8fd8\u53ef\u4ee5\u7ee7\u7eed\u8ffd\u95ee\u5417\uff1f'}</div>
          <div className="home-guide__assistant-bubble is-answer">{'\u5f53\u7136\u53ef\u4ee5\u3002\u6211\u4f1a\u7ed3\u5408\u6559\u6750\u8bc1\u636e\u62c6\u89e3\u601d\u8def\uff0c\u5e76\u63a8\u8350\u5bf9\u5e94\u7684\u89c6\u9891\u548c\u7ec3\u4e60\u3002'}</div>
          <div className="home-guide__assistant-live-thinking"><Bot size={15} />{'正在根据学习数据整理建议…'}</div>
        </div>
        <span className="home-guide__assistant-live-scrollbar" aria-hidden="true"><i /></span>
      </main>
      <AnimatedDemoPointer className="home-guide__destination-pointer--assistant-scroll" />
    </div>
  );
}

function TextbookScrollDemo() {
  return (
    <div className="home-guide__textbook-demo" data-testid="textbook-scroll-demo">
      <DemoPageHeader title="学习工坊 · 教材学习" />
      <div className="home-guide__textbook-viewport">
        <div className="home-guide__textbook-track">
          <section className="home-guide__textbook-plan">
            <div><span><Map size={14} /> LEARNING PLAN</span><strong>按长期计划推进教材学习</strong><small>当前阶段：中药方剂 · 4 本计划教材</small></div>
            <div><span><BookOpen size={14} /> 现在继续</span><strong>该继续学习《中药学》</strong><small>当前任务：中药性能与配伍规律</small></div>
          </section>
          <TextbookLibrary books={GUIDE_TEXTBOOKS} onOpen={() => {}} />
        </div>
        <span className="home-guide__textbook-scrollbar" aria-hidden="true"><i /></span>
        <AnimatedDemoPointer className="home-guide__destination-pointer--textbook" />
      </div>
    </div>
  );
}

function ConsultationLiveDemo({ playing }) {
  return (
    <div className="home-guide__patient-demo" data-testid="consultation-live-demo">
      <div className="home-guide__patient-scale">
        <SimulatedPatientChat showBack={false} guideDemo guidePlaying={playing} />
      </div>
    </div>
  );
}

function SmartPaperScrollDemo() {
  return (
    <div className="home-guide__paper-demo" data-testid="smart-paper-scroll-demo">
      <div className="home-guide__paper-scale">
        <div className="home-guide__paper-track">
          <DemoPageHeader title="练习工坊 · 智能组卷" />
          <SmartPaperPanel guideDemo />
        </div>
      </div>
      <span className="home-guide__paper-scrollbar" aria-hidden="true"><i /></span>
      <AnimatedDemoPointer className="home-guide__destination-pointer--paper-scroll" />
    </div>
  );
}

function ProfileInsightsDemo() {
  return (
    <div className="home-guide__insights-demo" data-testid="profile-insights-demo">
      <DemoPageHeader title="个性数据 · 学情报告" />
      <div className="home-guide__insights-grid">
        <article className="home-guide__radar-card">
          <header><BarChart3 size={14} aria-hidden="true" /><strong>能力结构</strong><span>雷达图</span></header>
          <RadarChart dimensions={GUIDE_DIMENSIONS} />
        </article>
        <div className="home-guide__insights-side">
          <div className="home-guide__trend-card"><LearningTrendDualAxisChart series={GUIDE_TREND_SERIES.slice(-14)} /></div>
          <div className="home-guide__heatmap-card"><LearningActivityHeatmap series={GUIDE_TREND_SERIES} /></div>
        </div>
      </div>
    </div>
  );
}

function ProfileEditDemo() {
  return (
    <div className="home-guide__profile-demo" data-testid="profile-edit-demo">
      <DemoPageHeader title="个性数据 · 编辑画像" />
      <div className="home-guide__profile-viewport">
        <div className="home-guide__profile-track">
          <section className="home-guide__profile-heading"><strong>我的学习画像</strong><div><span className="home-guide__profile-completeness">画像完整度 <b>88%</b></span><button type="button">编辑画像</button></div></section>
          <div className="home-guide__profile-columns">
            <article><h3>基础信息</h3><p>专业背景 - 中医药相关专业</p><p>当前基础 - 正在学习中医专业</p><p>学习目标 - 中医执业医师资格考试</p></article>
            <article><h3>学习偏好</h3><p>资源偏好 - 案例训练、章节训练</p><p>当前困难 - 方剂组成混淆</p><p>学习习惯 - 章节训练</p></article>
          </div>
        </div>
        <AnimatedDemoPointer className="home-guide__destination-pointer--profile-edit" />
      </div>
    </div>
  );
}

function ClinicalChapterDemo() {
  return (
    <div className="home-guide__clinical-demo" data-testid="clinical-chapter-demo">
      <DemoPageHeader title="???? - ?????" />
      <div className="home-guide__clinical-viewport"><div className="home-guide__clinical-track">
        <div className="home-guide__clinical-screen"><div className="home-guide__clinical-main"><span>??? - ??????</span><h3>??? - ????</h3><p>?????????????????????????</p></div><aside><strong>????</strong><div className="home-guide__clinical-video">??<small>??????</small></div></aside></div>
        <div className="home-guide__clinical-screen home-guide__clinical-screen--detail"><div className="home-guide__clinical-main"><span>???????????</span><h3>?????????????</h3><p>??????????????????????????</p></div><aside><strong>????</strong><div className="home-guide__clinical-video">??<small>?????????????</small></div></aside></div>
      </div></div>
    </div>
  );
}

function ClinicalImageDemo() {
  return (
    <div className="home-guide__clinical-demo home-guide__clinical-image-demo" data-testid="clinical-image-demo">
      <img src="/design-images/home/resource-search.png" alt="临床中医学第一章第一节学习内容" />
    </div>
  );
}

function ComprehensivePaperDemo() {
  return (
    <div className="home-guide__comprehensive-demo" data-testid="comprehensive-paper-demo">
      <DemoPageHeader title="练习工坊 · 综合套题" />
      <div className="home-guide__comprehensive-viewport"><div className="home-guide__comprehensive-track">
        <section className="home-guide__paper-selector"><h3>选择职业资格考试</h3><button type="button">中医执业药师职业资格考试</button><button type="button">中医执业医师资格考试</button></section>
        <section className="home-guide__paper-years"><h3>历年真题</h3><p>按年份查看完整试卷与解析</p>{['2025 年真题','2024 年真题','2023 年真题','2022 年真题'].map((item) => <div key={item}>{item}<span>查看试卷 ��</span></div>)}</section>
      </div><span className="home-guide__comprehensive-scrollbar" aria-hidden="true"><i /></span><AnimatedDemoPointer className="home-guide__destination-pointer--comprehensive" /></div>
    </div>
  );
}

function getPreview(scene) {
  if (scene.view === 'home-nav') return { eyebrow: '顶部导航', heading: '六大功能模块', summary: '从目标设定到数据复盘，覆盖完整学习流程。', items: TOP_MODULES, layout: 'grid' };
  if (scene.view === 'home-capabilities') return { eyebrow: '平台能力', heading: '四大核心功能', summary: '四项能力相互协同，让每一次学习形成下一步行动。', items: CORE_CAPABILITIES, layout: 'grid' };
  if (scene.view === 'certificates') return { eyebrow: '目标设置', heading: '选择你的资格考试目标', summary: '左侧说明目标作用，右侧列出平台支持的五类证书。', items: CERTIFICATES, layout: 'certificates' };
  if (scene.view.startsWith('path')) return { eyebrow: '个性化路径', heading: '中医执业医师学习路线', summary: '按照掌握情况动态安排阶段与任务。', items: PATH_ITEMS, layout: 'list' };
  if (scene.view.startsWith('practice')) return { eyebrow: '学习工具', heading: '选择适合当前问题的工具', summary: '携带当前学习上下文进入工具，无需重复说明。', items: PRACTICE_ITEMS, layout: 'grid' };
  if (scene.view.startsWith('training')) return { eyebrow: '练习中心', heading: '选择本次练习模式', summary: '练习结果会同步更新错题记录和能力数据。', items: TRAINING_ITEMS, layout: 'grid' };
  if (scene.view.startsWith('profile')) return { eyebrow: '学习数据', heading: '本周学习表现', summary: '专注 6.4 小时 · 任务完成率 86% · 掌握度 74%', items: [{ icon: BarChart3, label: '成长趋势', meta: '较上周提升 8%', selected: true }, { icon: Target, label: '薄弱知识点', meta: '5 个待加强' }], layout: 'list' };
  return { eyebrow: '平台能力', heading: '四项能力贯穿学习过程', summary: '从规划到反馈，各能力模块相互协同。', items: CORE_CAPABILITIES, layout: 'grid' };
}

function DestinationPreview({ step, scene, sceneIndex, playing }) {
  if (scene.view === 'short-path') return <ShortPathDemo />;
  if (scene.view === 'long-path') return <LongPathDemo />;
  if (scene.view === 'assistant-new-chat') return <AssistantNewChatDemo />;
  if (scene.view === 'textbook-scroll') return <TextbookScrollDemo />;
  if (scene.view === 'consultation-live') return <ConsultationLiveDemo playing={playing} />;
  if (scene.view === 'paper-scroll') return <SmartPaperScrollDemo />;
  if (scene.view === 'profile-insights') return <ProfileInsightsDemo />;
  if (scene.view === 'profile-edit') return <ProfileEditDemo />;
  if (scene.view === 'clinical-chapter') return <ClinicalChapterDemo />;
  if (scene.view === 'clinical-image') return <ClinicalImageDemo />;
  if (scene.view === 'comprehensive-paper') return <ComprehensivePaperDemo />;

  const preview = getPreview(scene);
  const emphasizeSecond = sceneIndex === step.scenes.length - 1 && step.scenes.length > 2;

  return (
    <div className={`home-guide__destination home-guide__destination--${preview.layout}`}>
      <header>
        <span className="home-guide__brand"><GraduationCap size={17} aria-hidden="true" /><strong>时珍智训</strong></span>
        <span>{step.label}</span>
        <span className="home-guide__sync"><CheckCircle2 size={14} aria-hidden="true" /> 数据已同步</span>
      </header>
      <div className="home-guide__destination-body">
        <div className="home-guide__destination-copy">
          <span>{preview.eyebrow}</span>
          <h3>{preview.heading}</h3>
          <p>{preview.summary}</p>
        </div>
        <div className={`home-guide__preview-items home-guide__preview-items--${preview.layout}`}>
          {preview.items.map((item, index) => {
            const Icon = item.icon;
            const active = item.selected || (emphasizeSecond && index === 1);
            return (
              <div key={item.label} className={`home-guide__preview-item ${active ? 'is-active' : ''}`}>
                <span><Icon size={18} aria-hidden="true" /></span>
                <div><strong>{item.label}</strong><small>{item.meta}</small></div>
                {active && <CheckCircle2 size={15} aria-hidden="true" />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function DemoSurface({ step, scene, sceneIndex, playing, onPause, onReplay }) {
  const source = scene.view === 'source';
  return (
    <div className={`home-guide__demo ${playing ? 'is-playing' : 'is-paused'}`} aria-label={`${step.label}自动演示`}>
      <div className={`home-guide__source ${source ? 'is-visible' : ''}`}>
        <img src="/design-images/home/user-homepage.png" alt={`${step.label}首页全貌`} />
        <div className={`home-guide__target ${source ? (step.sourceTargetClass || step.className) : step.className}`} aria-hidden="true" />
        <span className="home-guide__pointer" style={{ '--pointer-x': step.pointer.x, '--pointer-y': step.pointer.y }} aria-hidden="true"><MousePointer2 size={22} fill="currentColor" /></span>
      </div>
      <div className={`home-guide__destination-layer ${source ? '' : 'is-visible'}`} aria-hidden={source}>
        <DestinationPreview step={step} scene={scene} sceneIndex={sceneIndex} playing={playing} />
      </div>
      <div className="home-guide__controls" aria-label="演示控制">
        <button type="button" onClick={onPause} aria-label={playing ? '暂停演示' : '继续演示'}>{playing ? <Pause size={14} aria-hidden="true" /> : <ArrowRight size={14} aria-hidden="true" />}</button>
        <button type="button" onClick={onReplay} aria-label="回放演示"><RotateCcw size={14} aria-hidden="true" /></button>
      </div>
    </div>
  );
}

function HomepageSpotlight({ step, stepIndex, onClose, onNext, onPrevious }) {
  const [rect, setRect] = useState(null);
  const selector = SPOTLIGHT_TARGET_SELECTORS[step.key];
  const updateRect = useCallback(() => {
    const element = selector ? document.querySelector(selector) : null;
    if (element) {
      const next = element.getBoundingClientRect();
      const expanded = step.key === 'target-overview'
        ? { left: next.left - 20, top: next.top, width: next.width + 40, height: next.height + 18 }
        : { left: next.left, top: next.top, width: next.width, height: next.height };
      setRect(expanded);
      return;
    }
    const fallback = SPOTLIGHT_FALLBACKS[step.key];
    if (fallback) {
      setRect({
        left: window.innerWidth * fallback.left / 100,
        top: window.innerHeight * fallback.top / 100,
        width: window.innerWidth * fallback.width / 100,
        height: window.innerHeight * fallback.height / 100,
      });
    }
  }, [selector, step.key]);

  useLayoutEffect(() => {
    const frame = window.requestAnimationFrame(updateRect);
    window.addEventListener('resize', updateRect);
    window.addEventListener('scroll', updateRect, true);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', updateRect);
      window.removeEventListener('scroll', updateRect, true);
    };
  }, [updateRect]);

  const style = rect ? {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  } : undefined;
  const cardWidth = typeof window === 'undefined' ? 460 : Math.min(460, window.innerWidth - 32);
  const cardStyle = rect ? {
    left: Math.min(Math.max(rect.left, 16), window.innerWidth - cardWidth - 16),
    top: Math.min(rect.top + rect.height + 18, Math.max(16, window.innerHeight - 210)),
  } : { left: 16, top: 120 };

  const next = () => {
    if (stepIndex >= GUIDE_STEPS.length - 1) return onClose();
    onNext();
    return undefined;
  };

  return (
    <div className="home-guide__spotlight" role="dialog" aria-modal="true" aria-labelledby="home-guide-spotlight-title">
      {rect && <svg className="home-guide__spotlight-shade" aria-hidden="true" width="100%" height="100%" preserveAspectRatio="none">
        <defs>
          <mask id="home-guide-spotlight-mask">
            <rect width="100%" height="100%" fill="white" />
            <rect x={rect.left} y={rect.top} width={rect.width} height={rect.height} rx="12" fill="black" />
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="rgba(0,0,0,.57)" mask="url(#home-guide-spotlight-mask)" />
      </svg>}
      <div className="home-guide__spotlight-ring" style={style} aria-hidden="true" />
      <section className="home-guide__spotlight-card" style={cardStyle}>
        <div className="home-guide__kicker">首页引导 · {stepIndex + 1}/{GUIDE_STEPS.length}</div>
        <h2 id="home-guide-spotlight-title">{step.title}</h2>
        <p><HighlightedDescription text={step.description} stepIndex={stepIndex} /></p>
        <div className="home-guide__actions">
          <button type="button" className="home-guide__skip" onClick={onClose}>跳过所有</button>
          <div className="home-guide__step-actions">
            {stepIndex > 0 && <button type="button" className="home-guide__previous" onClick={onPrevious}>上一步</button>}
            <button type="button" className="home-guide__next" onClick={next}>{stepIndex >= GUIDE_STEPS.length - 1 ? '开始使用' : '下一步'}<ArrowRight size={16} aria-hidden="true" /></button>
          </div>
        </div>
      </section>
    </div>
  );
}

function prefersReducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
}

export default function HomeOnboardingGuide({ onClose }) {
  const [stepIndex, setStepIndex] = useState(0);
  const [sceneIndex, setSceneIndex] = useState(0);
  const [playing, setPlaying] = useState(() => !prefersReducedMotion());
  const [phase, setPhase] = useState('window');
  const step = GUIDE_STEPS[stepIndex];
  const scene = step.scenes[sceneIndex];
  const isLast = stepIndex === GUIDE_STEPS.length - 1;

  useEffect(() => {
    if (!playing) return undefined;
    const duration = scene.duration || (scene.view === 'source' ? SOURCE_DURATION : DESTINATION_DURATION);
    const timer = window.setTimeout(() => {
      if (sceneIndex < step.scenes.length - 1) setSceneIndex((index) => index + 1);
      else if (stepIndex < GUIDE_STEPS.length - 1) { setStepIndex((index) => index + 1); setSceneIndex(0); }
      else setPlaying(false);
    }, duration);
    return () => window.clearTimeout(timer);
  }, [playing, scene.view, scene.duration, sceneIndex, step.key, step.scenes.length, stepIndex]);

  const next = () => {
    setPhase('spotlight'); setStepIndex(0); setSceneIndex(0); setPlaying(false);
    return undefined;
  };

  const replay = () => { setStepIndex(0); setSceneIndex(0); setPlaying(true); };

  const nextSpotlight = () => setStepIndex((index) => Math.min(index + 1, GUIDE_STEPS.length - 1));
  const previousSpotlight = () => setStepIndex((index) => Math.max(index - 1, 0));

  if (phase === 'spotlight') {
    return typeof document === 'undefined'
      ? null
      : createPortal(
        <HomepageSpotlight step={step} stepIndex={stepIndex} onClose={onClose} onNext={nextSpotlight} onPrevious={previousSpotlight} />,
        document.body,
      );
  }

  const dialog = (
    <div className="home-guide" role="dialog" aria-modal="true" aria-label="新手引导演示">
      <section className="home-guide__modal home-guide__modal--demo-only">
        <div className="home-guide__kicker">新手引导 · {stepIndex + 1}/{GUIDE_STEPS.length}</div>
        <h1 id="home-guide-step-title" className="home-guide__step-title">{step.title}</h1>
        <div key={`${step.key}-${sceneIndex}`} className="home-guide__explanation home-guide__explanation--above" aria-live="polite" aria-atomic="true">
          <p><HighlightedDescription text={step.description} stepIndex={stepIndex} /></p>
        </div>
        <DemoSurface step={step} scene={scene} sceneIndex={sceneIndex} playing={playing} onPause={() => setPlaying((value) => !value)} onReplay={replay} />
        <div className="home-guide__step-progress" aria-label={`第 ${stepIndex + 1} 步，共 ${GUIDE_STEPS.length} 步`}>
          {GUIDE_STEPS.map((item, index) => <span key={item.key} className={index <= stepIndex ? 'is-active' : ''} />)}
        </div>
        <div className="home-guide__actions">
          <button type="button" className="home-guide__skip" onClick={onClose}>跳过所有</button>
          <button type="button" className="home-guide__next" onClick={next}>{isLast ? '开始使用' : '下一步'}<ArrowRight size={16} aria-hidden="true" /></button>
        </div>
      </section>
    </div>
  );

  return typeof document === 'undefined' ? null : createPortal(dialog, document.body);
}
