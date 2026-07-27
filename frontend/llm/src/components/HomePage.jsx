import React from 'react';
import {
  ArrowRight,
  BookOpenCheck,
  Bot,
  BrainCircuit,
  ChartNoAxesCombined,
  CircleCheckBig,
  DatabaseZap,
  LibraryBig,
  Network,
  SearchCheck,
  ShieldCheck,
  Sparkles,
  Target,
} from 'lucide-react';

const AGENTS = [
  { icon: Network, name: '规划调度', detail: '识别学习意图，组织本次协作链路。' },
  { icon: ChartNoAxesCombined, name: '学情诊断', detail: '结合画像、计划和学习行为判断重点。' },
  { icon: SearchCheck, name: '知识检索', detail: '从教材、题库和可信资料中定位证据。' },
  { icon: Sparkles, name: '内容生成', detail: '生成知识讲解、练习、试卷与反馈。' },
  { icon: DatabaseZap, name: '记忆管理', detail: '压缩长对话并沉淀稳定的学习记忆。' },
  { icon: ShieldCheck, name: '质量审核', detail: '在发布前核验范围、证据和任务约束。' },
];

const CAPABILITIES = [
  {
    icon: BookOpenCheck,
    title: '循序学习',
    description: '沿资格考试教材路线推进阶段、教材、章节与知识点。',
    action: '进入学习工坊',
    page: 'qualification-route',
  },
  {
    icon: Target,
    title: '闭环训练',
    description: '完成专项练习、智能组卷、错题复盘与到期复习。',
    action: '进入训练工坊',
    page: 'training-workshop',
  },
  {
    icon: BrainCircuit,
    title: '智能助教',
    description: '让六个智能体根据任务灵活协作，并保留可查看的执行路径。',
    action: '开始对话',
    page: 'assistant',
  },
];

export default function HomePage({ currentUser, onNavigate }) {
  const displayName = currentUser?.display_name || currentUser?.username || '同学';

  return (
    <div className="platform-home">
      <section className="platform-home__hero" aria-labelledby="platform-home-title">
        <div className="platform-home__hero-copy">
          <span className="platform-home__eyebrow"><Bot size={16} aria-hidden="true" />六智能体协同学习系统</span>
          <h1 id="platform-home-title">承时珍医脉<br /><span>启智慧学习</span></h1>
          <p>{displayName}，欢迎回来。系统会把你的目标、计划、学习行为和知识证据连接起来，让每一次学习都能形成下一步行动。</p>
          <div className="platform-home__hero-actions">
            <button type="button" onClick={() => onNavigate({ page: 'assistant', params: { newConversation: true } })}>
              <Sparkles size={17} aria-hidden="true" />询问智能助教
            </button>
            <button type="button" onClick={() => onNavigate({ page: 'qualification-route', params: {} })}>
              查看今日学习<ArrowRight size={17} aria-hidden="true" />
            </button>
          </div>
          <div className="platform-home__trust">
            <span><CircleCheckBig size={15} />画像驱动</span>
            <span><CircleCheckBig size={15} />证据可追踪</span>
            <span><CircleCheckBig size={15} />结果经审核</span>
          </div>
        </div>

        <div className="platform-home__agent-orbit" aria-label="六智能体协作示意">
          <div className="platform-home__agent-core">
            <BrainCircuit size={34} aria-hidden="true" />
            <strong>学习任务</strong>
            <small>动态编排</small>
          </div>
          {AGENTS.map((agent, index) => {
            const Icon = agent.icon;
            return (
              <span key={agent.name} className="platform-home__agent-node" style={{ '--agent-index': index }}>
                <Icon size={18} aria-hidden="true" /><b>{agent.name}</b>
              </span>
            );
          })}
        </div>
      </section>

      <section className="platform-home__workflow" aria-labelledby="workflow-title">
        <header>
          <span>HOW IT WORKS</span>
          <h2 id="workflow-title">不是单次回答，而是一条持续学习闭环</h2>
          <p>任务由合适的智能体接力完成，可靠结果再写回计划、掌握度、复习队列与学习记忆。</p>
        </header>
        <div className="platform-home__agent-grid">
          {AGENTS.map((agent, index) => {
            const Icon = agent.icon;
            return (
              <article key={agent.name}>
                <span className="platform-home__agent-index">{String(index + 1).padStart(2, '0')}</span>
                <Icon size={23} aria-hidden="true" />
                <h3>{agent.name}</h3>
                <p>{agent.detail}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="platform-home__capabilities" aria-label="核心学习入口">
        {CAPABILITIES.map((item) => {
          const Icon = item.icon;
          return (
            <button key={item.title} type="button" onClick={() => onNavigate({ page: item.page, params: {} })}>
              <span><Icon size={24} aria-hidden="true" /></span>
              <strong>{item.title}</strong>
              <p>{item.description}</p>
              <em>{item.action}<ArrowRight size={15} aria-hidden="true" /></em>
            </button>
          );
        })}
      </section>

      <button
        type="button"
        className="platform-home__route-note"
        onClick={() => onNavigate({ page: 'qualification-route', params: {} })}
        aria-label="查看资格考试经典路线"
      >
        <LibraryBig size={21} aria-hidden="true" />
        <div>
          <strong>查看资格考试经典路线</strong>
          <p>请在顶部“资格考试路径”中选择考试，系统会打开对应的阶段—教材学习路线。</p>
        </div>
        <ArrowRight size={18} aria-hidden="true" />
      </button>
    </div>
  );
}
