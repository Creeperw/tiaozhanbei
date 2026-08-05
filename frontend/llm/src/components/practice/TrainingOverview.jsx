import React, { useState } from 'react';
import { ChevronRight, CircleAlert, CirclePlay, Clock3 } from 'lucide-react';
import {
  featuredTrainingCard,
  groupedTrainingCards,
  overviewTrainingCards,
  resumableTrainingCards,
  trainingIcons,
  utilityCards,
  normalizeTrainingOverviewStats,
} from './taskRegistry';

function TrainingBannerIllustration() {
  const [hint, setHint] = useState('快来跟我一起练习吧');
  const phrases = ['快来跟我一起练习吧', '今天也要加油哦', '温故而知新', '学而时习之', '坚持就是胜利'];
  return (
    <div className="practice-overview__illustration">
      <span className="practice-overview__character-note" aria-live="polite">{hint}</span>
      <button
        type="button"
        className="practice-overview__character-button"
        onClick={() => {
          const next = phrases.filter((phrase) => phrase !== hint);
          setHint(next[Math.floor(Math.random() * next.length)]);
        }}
        aria-label="和李时珍互动"
      >
        <img
          className="practice-overview__character"
          src="/assistant-character/lizhizhen-center-cutout.png"
          alt=""
        />
      </button>
    </div>
  );
}
function OverviewSummaryMetric({ icon, label, value, hint, tone = 'green', progress }) {
  return (
    <div className={`practice-overview__summary-metric practice-overview__summary-metric--${tone}`}>
      <span className="practice-overview__summary-icon">{React.createElement(icon, { 'aria-hidden': true, size: 22 })}</span>
      <span className="practice-overview__summary-copy">
        <small>{label}</small>
        <strong>{value}</strong>
        <em>{hint}</em>
        {progress !== undefined && (
          <span
            className="practice-overview__progress"
            role="progressbar"
            aria-label="今日目标完成度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <span style={{ width: `${progress}%` }} />
          </span>
        )}
      </span>
    </div>
  );
}

export default function TrainingOverview({ onOpenModule, overviewStats, weakKnowledgePoints = [] }) {
  const stats = normalizeTrainingOverviewStats(overviewStats);
  const recentCard = resumableTrainingCards.find((card) => card.key === stats.recentTaskKey)
    || overviewTrainingCards[2];
  const formatPercent = (value) => value === null ? '--' : `${value}%`;
  const formatHours = (value) => value === null ? '--' : `${value} 小时`;
  const formatQuestions = (value) => value === null ? '累计练习待接入' : `累计练习 ${value} 题`;
  const { CalendarDays: CalendarIcon, Target: TargetIcon } = trainingIcons;

  return (
    <section className="practice-overview" aria-labelledby="practice-overview-title">
      <header className="practice-overview__banner">
        <div className="practice-overview__hero-copy">
          <span className="practice-overview__greeting"><span>准备开始今天的练习</span> <span aria-hidden="true">🌿</span></span>
          <h1 id="practice-overview-title">练习工坊</h1>
          <p>今日建议完成 <strong>{stats.todayGoal}</strong> 道综合题，预计 <strong>15</strong> 分钟</p>
          <div className="practice-overview__hero-actions">
            <button type="button" className="practice-overview__primary-action" onClick={() => onOpenModule(featuredTrainingCard)}>
              <CirclePlay aria-hidden="true" size={18} />开始今日练习
            </button>
            <button type="button" className="practice-overview__secondary-action" onClick={() => onOpenModule(recentCard)}>
              <Clock3 aria-hidden="true" size={17} />继续上次练习
            </button>
          </div>
        </div>
        <TrainingBannerIllustration />
      </header>

      <div className="practice-overview__layout">
        <section className="practice-overview__main" aria-label="练习模块">
          <div className="practice-overview__training-groups">
            {groupedTrainingCards.map((group) => (
              <section key={group.key} className="practice-overview__training-group" aria-labelledby={`training-group-${group.key}`}>
                <h2 id={`training-group-${group.key}`}>{group.title}</h2>
                <div className="practice-overview__training-grid">
                  {group.cards.map((card) => {
                    const Icon = card.icon;
                    return (
                      <button key={card.key} type="button" className={`practice-overview__training-card practice-overview__training-card--${card.tone}`} onClick={() => onOpenModule(card)}>
                        <span className="practice-overview__card-icon"><Icon aria-hidden="true" size={26} /></span>
                        <span className="practice-overview__card-copy"><strong>{card.title}</strong><small>{card.description}</small></span>
                        <ChevronRight className="practice-overview__card-arrow" aria-hidden="true" size={19} />
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
            <section className="practice-overview__weak-points" aria-labelledby="practice-weak-points-title">
              <div className="practice-overview__weak-points-heading">
                <span className="practice-overview__weak-points-icon"><CircleAlert aria-hidden="true" size={20} /></span>
                <span>
                  <h2 id="practice-weak-points-title">最近练习薄弱知识点</h2>
                  <p>根据近 30 天练习结果，优先巩固掌握度较低的内容</p>
                </span>
              </div>
              {weakKnowledgePoints.length > 0 ? (
                <ol className="practice-overview__weak-points-list">
                  {weakKnowledgePoints.map((item, index) => (
                    <li key={item.kpId || item.kpName}>
                      <span className="practice-overview__weak-point-rank">{index + 1}</span>
                      <span className="practice-overview__weak-point-copy">
                        <strong>{item.kpName}</strong>
                        <small>{item.reason}</small>
                      </span>
                      <button
                        type="button"
                        className="practice-overview__weak-point-action"
                        onClick={() => onOpenModule({ key: 'topic_training', kpId: item.kpId, kpName: item.kpName })}
                        aria-label={`去专项巩固：${item.kpName}`}
                      >
                        去专项巩固 <ChevronRight aria-hidden="true" size={14} />
                      </button>
                      <span className="practice-overview__weak-point-score">
                        <small>掌握度</small>
                        <strong>{item.masteryScore === null ? '待评估' : `${item.masteryScore}%`}</strong>
                      </span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="practice-overview__weak-points-empty">完成练习并提交评分后，这里将展示需要优先巩固的知识点。</p>
              )}
            </section>
          </div>
        </section>

        <aside className="practice-overview__utilities" aria-label="学习工具">
          <section className="practice-overview__utility-panel" aria-labelledby="practice-utilities-title">
            <h2 id="practice-utilities-title" className="practice-overview__utilities-title">其他功能</h2>
            <div className="practice-overview__utility-list">
              {utilityCards.filter((card) => card.available && card.key !== 'study_notes').map((card) => {
                const Icon = card.icon;
                return (
                  <button key={card.title} type="button" className="practice-overview__utility-card" onClick={() => onOpenModule(card)}>
                    <span className="practice-overview__utility-icon"><Icon aria-hidden="true" size={22} /></span>
                    <span><strong>{card.title}</strong><small>{card.description}</small></span>
                    <ChevronRight aria-hidden="true" size={18} />
                  </button>
                );
              })}
            </div>
          </section>

          <section className="practice-overview__stats-panel" aria-labelledby="practice-stats-title">
            <h2 id="practice-stats-title" className="practice-overview__utilities-title">学习数据</h2>
            <div className="practice-overview__hero-summary" role="region" aria-label="学习概览">
              <OverviewSummaryMetric icon={CalendarIcon} label="近 30 天练习" value={stats.windowPracticeCount === null ? '--' : `${stats.windowPracticeCount} 题`} hint="正式审核完成题目" />
              <OverviewSummaryMetric icon={TargetIcon} label="平均正确率" value={formatPercent(stats.averageAccuracy)} hint={stats.averageAccuracy === null ? '暂无数据' : '继续保持'} />
              <OverviewSummaryMetric icon={Clock3} label="累计学习" value={formatHours(stats.totalHours)} hint={formatQuestions(stats.totalQuestions)} tone="purple" />
            </div>
          </section>
        </aside>
      </div>
    </section>
  );
}

