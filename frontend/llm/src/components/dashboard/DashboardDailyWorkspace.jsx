import React, { useState } from 'react';
import {
  CalendarDays,
  CheckCircle2,
  Circle,
  Clock3,
  LockKeyhole,
  Route,
  Sparkles,
} from 'lucide-react';

const RAIL_TAB_IDS = {
  today: 'learning-work-rail-tab-today',
  assistant: 'learning-work-rail-tab-assistant',
};

const RAIL_PANEL_IDS = {
  today: 'learning-work-rail-panel-today',
  assistant: 'learning-work-rail-panel-assistant',
};

function ScheduleIcon({ state }) {
  if (state === 'completed') return <CheckCircle2 aria-hidden="true" size={16} />;
  if (state === 'current') return <Clock3 aria-hidden="true" size={16} />;
  if (state === 'blocked') return <LockKeyhole aria-hidden="true" size={15} />;
  return <Circle aria-hidden="true" size={14} />;
}

const SCHEDULE_STATE_LABEL = {
  completed: '已完成',
  current: '进行中',
  pending: '待开始',
  blocked: '已锁定',
};

export default function DashboardDailyWorkspace({
  showFocus = true,
  fullscreen = false,
  greeting,
  focus,
  schedule = [],
  feedback = [],
  primaryAction = null,
  secondaryAction = null,
  todayTaskContent = null,
  pathTopContent,
  pathControls,
  pathContent,
  pathHint = '单击查看计划 · 双击进入知识图谱',
  trainingContent,
  assistantContent,
  assistantCollapsed = false,
  assistantDocked = true,
  railTab,
  onRailTabChange,
}) {
  const [internalRailTab, setInternalRailTab] = useState('today');
  const activeRailTab = railTab === 'today' || railTab === 'assistant'
    ? railTab
    : internalRailTab;

  const selectRailTab = (nextTab) => {
    if (railTab !== 'today' && railTab !== 'assistant') setInternalRailTab(nextTab);
    onRailTabChange?.(nextTab);
  };

  const handleRailTabKeyDown = (event) => {
    const nextTab = {
      ArrowLeft: activeRailTab === 'today' ? 'assistant' : 'today',
      ArrowRight: activeRailTab === 'assistant' ? 'today' : 'assistant',
      Home: 'today',
      End: 'assistant',
    }[event.key];
    if (!nextTab) return;
    event.preventDefault();
    selectRailTab(nextTab);
    document.getElementById(RAIL_TAB_IDS[nextTab])?.focus();
  };

  const scheduleContent = (
    <div className="dashboard-daily__schedule">
      {todayTaskContent}
      {(schedule.length > 0 || !todayTaskContent) && <header>
        <div className="dashboard-daily__schedule-title">
          <span><CalendarDays aria-hidden="true" size={15} />Today</span>
          <h2>{todayTaskContent ? '复习安排' : '今日安排'}</h2>
        </div>
        <small>{schedule.length} 项</small>
      </header>}
      {schedule.length > 0 ? (
        <ol>
          {schedule.map((item, index) => (
            <li key={item.id} data-state={item.state}>
              <i><ScheduleIcon state={item.state} /></i>
              <div>
                <span className="sr-only">任务状态：{SCHEDULE_STATE_LABEL[item.state] || '待开始'}</span>
                <span>{item.time || `第 ${index + 1} 项`}</span>
                <strong>{item.title}</strong>
                {item.description && <p>{item.description}</p>}
                {item.duration && <small>{item.duration}</small>}
              </div>
            </li>
          ))}
        </ol>
      ) : !todayTaskContent ? (
        <div className="dashboard-daily__schedule-empty">
          <CheckCircle2 aria-hidden="true" size={22} />
          <p>今天还没有待办任务</p>
          <small>你可以从学习路径中选择一个节点开始</small>
        </div>
      ) : null}
    </div>
  );

  const feedbackContent = (
    <section className="dashboard-daily__feedback" aria-label="昨日学习反馈">
      <header><Sparkles aria-hidden="true" size={16} /><span>昨日学习反馈</span></header>
      {feedback.length > 0 ? (
        <ul>
          {feedback.map((item) => (
            <li key={item.key}><span>{item.label}</span><strong>{item.value}</strong></li>
          ))}
        </ul>
      ) : <p>完成一次学习后，这里会生成反馈摘要</p>}
    </section>
  );

  return (
    <div
      className={`dashboard-daily${showFocus ? '' : ' dashboard-daily--workspace-only'}${!showFocus && fullscreen ? ' dashboard-daily--training-workshop' : ''}`}
      data-layout={fullscreen ? 'fullscreen' : undefined}
      data-testid="dashboard-daily"
    >
      {showFocus && <section className="dashboard-daily__focus" aria-label="今日核心任务">
        <div className="dashboard-daily__welcome">
          <span>Daily focus</span>
          <h1>{greeting}</h1>
        </div>
        <div className="dashboard-daily__focus-copy">
          <span>今日核心任务</span>
          <h2>{focus.title}</h2>
          {focus.description && <p>{focus.description}</p>}
        </div>
        <div className="dashboard-daily__focus-actions">
          {focus.duration && <small><Clock3 aria-hidden="true" size={14} />预计 {focus.duration}</small>}
          <div>{secondaryAction}{primaryAction}</div>
        </div>
      </section>}

      <section
        className="dashboard-daily__workspace"
        aria-label="今日学习工作区"
        data-training-layout={!showFocus && fullscreen ? 'right-stack-raised' : undefined}
        data-assistant-collapsed={String(assistantCollapsed)}
        data-assistant-docked={String(assistantDocked)}
        data-right-column={!showFocus && fullscreen ? 'stable' : undefined}
      >
        <div className="dashboard-daily__learning-column" data-testid="dashboard-learning-column">
          {pathTopContent}
          <section className="dashboard-daily__path" aria-label="学习路径区域">
            <header className="dashboard-daily__path-header" role="group" aria-label="学习路径控制区">
              <div className="dashboard-daily__path-title">
                <span><Route aria-hidden="true" size={15} />Learning path</span>
                <h2>学习路径</h2>
              </div>
              <div className="dashboard-daily__path-controls">{pathControls}</div>
              <small className="dashboard-daily__path-hint">{pathHint}</small>
            </header>
            <div className="dashboard-daily__path-stage">{pathContent}</div>
          </section>
          {trainingContent}
          {feedbackContent}
        </div>

        <aside className="dashboard-daily__work-rail" aria-label="学习工作栏">
          <div className="dashboard-daily__rail-tabs" role="tablist" aria-label="学习工作栏视图">
            <button
              id={RAIL_TAB_IDS.today}
              type="button"
              role="tab"
              aria-selected={activeRailTab === 'today'}
              aria-controls={RAIL_PANEL_IDS.today}
              tabIndex={activeRailTab === 'today' ? 0 : -1}
              onClick={() => selectRailTab('today')}
              onKeyDown={handleRailTabKeyDown}
            >今日任务</button>
            <button
              id={RAIL_TAB_IDS.assistant}
              type="button"
              role="tab"
              aria-selected={activeRailTab === 'assistant'}
              aria-controls={RAIL_PANEL_IDS.assistant}
              tabIndex={activeRailTab === 'assistant' ? 0 : -1}
              onClick={() => selectRailTab('assistant')}
              onKeyDown={handleRailTabKeyDown}
            >AI 助教</button>
          </div>
          <div className="dashboard-daily__rail-content">
            <div
              id={RAIL_PANEL_IDS.today}
              className="dashboard-daily__rail-panel"
              role="tabpanel"
              aria-label="今日任务"
              aria-labelledby={RAIL_TAB_IDS.today}
              hidden={activeRailTab !== 'today'}
            >{scheduleContent}</div>
            <div
              id={RAIL_PANEL_IDS.assistant}
              className="dashboard-daily__rail-panel"
              role="tabpanel"
              aria-label="AI 助教"
              aria-labelledby={RAIL_TAB_IDS.assistant}
              hidden={activeRailTab !== 'assistant'}
            >{assistantContent}</div>
          </div>
        </aside>
      </section>
    </div>
  );
}
