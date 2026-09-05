import React from 'react';
import {
  CheckCircle2,
  Circle,
  Route,
  Target,
  X,
} from 'lucide-react';

export default function LearningPlanRail({
  layout = '',
  node,
  summary,
  routeNodes = [],
  error = '',
  loading = false,
  onRetry,
  onClose,
  onStartLearning,
}) {
  if (!node) return null;
  const total = Number(summary?.total_count || 0);
  const completed = Number(summary?.completed_count || 0);
  const incomplete = Number(summary?.incomplete_count || 0);
  const mastery = summary?.average_mastery;
  const plannedNodes = routeNodes.length ? routeNodes : [node];
  const currentIndex = Math.max(0, plannedNodes.findIndex((item) => item.membership_id === node.membership_id));

  return (
    <aside className="learning-plan-rail" data-layout={layout || undefined} aria-label={`${node.title}学习规划`}>
      <section className="learning-plan-card learning-plan-card--current">
        <header>
          <span><Target aria-hidden="true" size={15} />当前任务</span>
          <button type="button" aria-label="关闭学习规划" onClick={onClose}><X aria-hidden="true" size={14} /></button>
        </header>
        <span className="learning-plan-card__status">{summary?.status === 'completed' ? '已完成' : '进行中'}</span>
        <h2>{node.title}</h2>

        {error ? (
          <div className="learning-plan-card__error" role="alert">
            <p>{error}</p>
            <button type="button" onClick={onRetry}>重试学习摘要</button>
          </div>
        ) : loading ? (
          <p className="learning-plan-card__muted">正在同步学习摘要…</p>
        ) : (
          <>
            <p className="learning-plan-card__muted">
              {total ? `第 ${completed} / ${total} 个知识点` : '尚未形成可评估记录'}
            </p>
            <div className="learning-plan-card__progress-row">
              <span>章节进度</span>
              <strong>{mastery == null ? '尚未评估' : `${mastery}%`}</strong>
            </div>
            <div className="learning-plan-card__progress">
              <i style={{ width: `${mastery == null ? 0 : Math.max(0, Math.min(100, mastery))}%` }} />
            </div>
            <ul>
              <li><CheckCircle2 aria-hidden="true" size={13} />{completed} 个已完成</li>
              <li><Circle aria-hidden="true" size={13} />{incomplete} 个待完成</li>
              <li><Circle aria-hidden="true" size={13} />{summary?.review_due_count || 0} 个待复习</li>
            </ul>
          </>
        )}
        <button type="button" className="learning-plan-card__primary" onClick={() => onStartLearning(node)}>
          开始练习
        </button>
      </section>

      <section className="learning-plan-card learning-plan-card--route">
        <header>
          <span><Route aria-hidden="true" size={15} />学习路径</span>
        </header>
        <ol>
          {plannedNodes.map((item, index) => (
            <li
              key={item.membership_id || `${item.title}-${index}`}
              className={index === currentIndex ? 'is-current' : index < currentIndex ? 'is-complete' : 'is-upcoming'}
            >
              <i />
              <span>{item.title}</span>
            </li>
          ))}
        </ol>
      </section>
    </aside>
  );
}
