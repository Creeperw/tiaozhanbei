import React from 'react';
import {
  CalendarDays,
  CheckCircle2,
  Circle,
  Clock3,
  LockKeyhole,
  Route,
  Sparkles,
} from 'lucide-react';

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
  pathTopContent,
  pathContent,
  assistantContent,
  assistantCollapsed = false,
  assistantDocked = true,
}) {
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
          <div>
            {secondaryAction}
            {primaryAction}
          </div>
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
        <aside className="dashboard-daily__schedule" aria-label="今日安排">
          <header>
            <div className="dashboard-daily__schedule-title">
              <span><CalendarDays aria-hidden="true" size={15} />Today</span>
              <h2>今日安排</h2>
            </div>
            <small>{schedule.length} 项</small>
          </header>
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
          ) : (
            <div className="dashboard-daily__schedule-empty">
              <CheckCircle2 aria-hidden="true" size={22} />
              <p>今天还没有待办任务</p>
              <small>你可以从学习路径中选择一个节点开始</small>
            </div>
          )}
        </aside>

        <div className="dashboard-daily__learning-column" data-testid="dashboard-learning-column">
          {pathTopContent}
          <section className="dashboard-daily__path" aria-label="学习路径区域">
            <header>
              <div className="dashboard-daily__path-title">
                <span><Route aria-hidden="true" size={15} />Learning path</span>
                <h2>学习路径</h2>
              </div>
              <small>单击查看计划 · 双击进入知识图谱</small>
            </header>
            <div className="dashboard-daily__path-stage">{pathContent}</div>
          </section>
        </div>

        <section className="dashboard-daily__assistant" aria-label="智能助教栏">
          {assistantContent}
        </section>
      </section>

      <section className="dashboard-daily__feedback" aria-label="昨日学习反馈">
        <header>
          <Sparkles aria-hidden="true" size={16} />
          <span>昨日学习反馈</span>
        </header>
        {feedback.length > 0 ? (
          <ul>
            {feedback.map((item) => (
              <li key={item.key}><span>{item.label}</span><strong>{item.value}</strong></li>
            ))}
          </ul>
        ) : (
          <p>完成一次学习后，这里会生成反馈摘要</p>
        )}
      </section>
    </div>
  );
}
