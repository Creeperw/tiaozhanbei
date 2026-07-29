import React, { useEffect, useState } from 'react';
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
  Play,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
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
  { icon: ClipboardCheck, label: '训练工坊', meta: '完成专项题与模拟训练' },
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

const GUIDE_STEPS = [
  {
    key: 'home', label: '平台首页', className: 'home-guide-target--home', pointer: { x: '34.38%', y: '4.87%' },
    scenes: [
      { title: '定位平台首页', description: '点击顶部“平台首页”，可以随时从其他功能返回统一的学习入口。', details: ['快速回到学习工作台', '重新查看当前任务与平台能力'], view: 'source' },
      { title: '认识六大顶部模块', description: '顶部导航集中连接平台首页、学习目标、学习路径、学习工坊、训练工坊和个性数据。', details: ['六个模块覆盖完整学习流程', '在任意页面都能快速切换'], view: 'home-nav' },
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
    key: 'training', label: '训练工坊', className: 'home-guide-target--training', pointer: { x: '59.26%', y: '4.70%' },
    scenes: [
      { title: '打开训练工坊', description: '点击“训练工坊”选择题目训练、模拟患者、错题变式或试卷生成。', details: ['四种训练模式统一入口', '训练记录自动归入学习数据'], view: 'source' },
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

const PATH_ITEMS = [
  { icon: BookOpen, label: '基础巩固', meta: '已完成 · 100%', selected: true },
  { icon: Map, label: '方剂学专题', meta: '进行中 · 72%' },
  { icon: ClipboardCheck, label: '临床辨证实战', meta: '下一阶段' },
];

const PRACTICE_ITEMS = [
  { icon: Bot, label: '智能助教', meta: '即时答疑与追问', selected: true },
  { icon: Network, label: '知识图谱', meta: '查看知识关联' },
  { icon: Database, label: '个人知识库', meta: '沉淀笔记与资料' },
  { icon: Upload, label: '资料上传', meta: '建立个人学习资源' },
];

const TRAINING_ITEMS = [
  { icon: ClipboardCheck, label: '题目训练', meta: '按知识点专项练习', selected: true },
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
  { id: 'herbs', book: '中药学', stage_title: '中药方剂' },
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
    <div className="home-guide__assistant-demo" data-testid="assistant-new-chat-demo">
      <aside className="home-guide__assistant-rail" aria-label="演示会话列表">
        <div className="home-guide__assistant-identity"><span><HeartPulse size={16} /></span><strong>时珍智训智能助教</strong></div>
        <button type="button" className="home-guide__assistant-new"><Plus size={14} />新对话</button>
        <small>历史记录</small>
        <div className="home-guide__assistant-session is-current"><MessageCircle size={13} /><span>方剂配伍复习</span></div>
        <div className="home-guide__assistant-session"><MessageCircle size={13} /><span>中医诊断学习计划</span></div>
        <div className="home-guide__assistant-session home-guide__assistant-session--created"><Sparkles size={13} /><span>新对话</span></div>
      </aside>
      <main className="home-guide__assistant-main">
        <header>
          <span className="home-guide__assistant-status" />
          <strong className="home-guide__assistant-title-before">方剂配伍复习</strong>
          <strong className="home-guide__assistant-title-after">新对话</strong>
        </header>
        <div className="home-guide__assistant-conversation-before">
          <div className="home-guide__assistant-bubble">请帮我梳理君臣佐使的配伍原则。</div>
          <div className="home-guide__assistant-bubble is-answer">可以从主治病机、核心功效和配伍关系三个层次理解。</div>
        </div>
        <div className="home-guide__assistant-conversation-after">
          <span><Bot size={24} /></span>
          <h3>今天想从哪里开始？</h3>
          <p>新会话已创建，可以提出新的学习问题。</p>
          <div><span>输入你的问题</span><button type="button" aria-label="发送演示问题"><Send size={14} /></button></div>
        </div>
      </main>
      <AnimatedDemoPointer className="home-guide__destination-pointer--new-chat" />
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
          <DemoPageHeader title="训练工坊 · 智能组卷" />
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

function getPreview(scene) {
  if (scene.view === 'home-nav') return { eyebrow: '顶部导航', heading: '六大功能模块', summary: '从目标设定到数据复盘，覆盖完整学习流程。', items: TOP_MODULES, layout: 'grid' };
  if (scene.view === 'home-capabilities') return { eyebrow: '平台能力', heading: '四大核心功能', summary: '四项能力相互协同，让每一次学习形成下一步行动。', items: CORE_CAPABILITIES, layout: 'grid' };
  if (scene.view === 'certificates') return { eyebrow: '目标设置', heading: '选择你的资格考试目标', summary: '左侧说明目标作用，右侧列出平台支持的五类证书。', items: CERTIFICATES, layout: 'certificates' };
  if (scene.view.startsWith('path')) return { eyebrow: '个性化路径', heading: '中医执业医师学习路线', summary: '按照掌握情况动态安排阶段与任务。', items: PATH_ITEMS, layout: 'list' };
  if (scene.view.startsWith('practice')) return { eyebrow: '学习工具', heading: '选择适合当前问题的工具', summary: '携带当前学习上下文进入工具，无需重复说明。', items: PRACTICE_ITEMS, layout: 'grid' };
  if (scene.view.startsWith('training')) return { eyebrow: '训练中心', heading: '选择本次训练模式', summary: '训练结果会同步更新错题记录和能力数据。', items: TRAINING_ITEMS, layout: 'grid' };
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

function DemoSurface({ step, scene, sceneIndex, playing, onToggle, onReplay }) {
  const source = scene.view === 'source';
  return (
    <div className={`home-guide__demo ${playing ? 'is-playing' : 'is-paused'}`} aria-label={`${step.label}自动演示`}>
      <div className={`home-guide__source ${source ? 'is-visible' : ''}`}>
        <img src="/design-images/home/user-homepage.png" alt={`${step.label}首页全貌`} />
        <div className={`home-guide__target ${step.className}`} aria-hidden="true" />
        <span className="home-guide__pointer" style={{ '--pointer-x': step.pointer.x, '--pointer-y': step.pointer.y }} aria-hidden="true"><MousePointer2 size={22} fill="currentColor" /></span>
      </div>
      <div className={`home-guide__destination-layer ${source ? '' : 'is-visible'}`} aria-hidden={source}>
        <DestinationPreview step={step} scene={scene} sceneIndex={sceneIndex} playing={playing} />
      </div>
      <div className="home-guide__controls">
        <button type="button" onClick={onToggle} aria-label={playing ? '暂停演示' : '继续演示'} title={playing ? '暂停演示' : '继续演示'}>{playing ? <Pause size={15} /> : <Play size={15} />}</button>
        <button type="button" onClick={onReplay} aria-label="重新播放当前演示" title="重新播放"><RotateCcw size={15} /></button>
      </div>
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
  const step = GUIDE_STEPS[stepIndex];
  const scene = step.scenes[sceneIndex];
  const isLast = stepIndex === GUIDE_STEPS.length - 1;

  useEffect(() => {
    if (!playing) return undefined;
    const duration = scene.view === 'source' ? SOURCE_DURATION : DESTINATION_DURATION;
    const timer = window.setTimeout(() => setSceneIndex((index) => (index + 1) % step.scenes.length), duration);
    return () => window.clearTimeout(timer);
  }, [playing, scene.view, step.key, step.scenes.length]);

  const next = () => {
    if (isLast) return onClose();
    setSceneIndex(0);
    setPlaying(!prefersReducedMotion());
    setStepIndex((index) => index + 1);
    return undefined;
  };

  const replay = () => {
    setSceneIndex(0);
    setPlaying(true);
  };

  const dialog = (
    <div className="home-guide" role="dialog" aria-modal="true" aria-labelledby="home-guide-title">
      <section className="home-guide__modal">
        <button type="button" className="home-guide__close" onClick={onClose} aria-label="关闭新手引导">×</button>
        <div className="home-guide__kicker">新手引导 · {stepIndex + 1}/{GUIDE_STEPS.length}</div>
        <DemoSurface step={step} scene={scene} sceneIndex={sceneIndex} playing={playing} onToggle={() => setPlaying((value) => !value)} onReplay={replay} />
        <div className="home-guide__step-progress" aria-label={`第 ${stepIndex + 1} 步，共 ${GUIDE_STEPS.length} 步`}>
          {GUIDE_STEPS.map((item, index) => <span key={item.key} className={index <= stepIndex ? 'is-active' : ''} />)}
        </div>
        <div key={`${step.key}-${sceneIndex}`} className="home-guide__explanation" aria-live="polite" aria-atomic="true">
          <span>演示 {sceneIndex + 1}/{step.scenes.length} · {step.label}</span>
          <h2 id="home-guide-title">{scene.title}</h2>
          <p>{scene.description}</p>
          <ul>{scene.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
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
