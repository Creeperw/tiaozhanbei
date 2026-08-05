import React, { useState } from 'react';
import {
  CalendarDays,
  ChevronRight,
  CirclePlay,
  Clock3,
  Target,
} from 'lucide-react';
import {
  findTrainingCard,
  normalizeTrainingOverviewStats,
  PRIMARY_TRAINING_GROUPS,
  UTILITY_CARDS,
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
        aria-label="和李时珍互动"
        onClick={() => {
          const next = phrases.filter((phrase) => phrase !== hint);
          setHint(next[Math.floor(Math.random() * next.length)]);
        }}
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

function SummaryMetric({ icon: Icon, label, value, hint, tone = 'green' }) {
  return (
    <div className={`practice-overview__summary-metric practice-overview__summary-metric--${tone}`}>
      <span className="practice-overview__summary-icon"><Icon aria-hidden="true" size={22} /></span>
      <span className="practice-overview__summary-copy">
        <small>{label}</small>
        <strong>{value}</strong>
        <em>{hint}</em>
      </span>
    </div>
  );
}

function TrainingCard({ card, onOpenModule }) {
  const Icon = card.icon;
  return (
    <button
      type="button"
      className={`practice-overview__training-card practice-overview__training-card--${card.tone || 'green'}`}
      onClick={() => onOpenModule(card)}
    >
      <span className="practice-overview__card-icon"><Icon aria-hidden="true" size={26} /></span>
      <span className="practice-overview__card-copy">
        <strong>{card.title}</strong>
        <small>{card.description}</small>
      </span>
      <ChevronRight className="practice-overview__card-arrow" aria-hidden="true" size={19} />
    </button>
  );
}

export default function TrainingOverview({ onOpenModule, overviewStats }) {
  const stats = normalizeTrainingOverviewStats(overviewStats);
  const recentCard = findTrainingCard(stats.recentTaskKey);
  const formatPercent = (value) => value === null ? '--' : `${value}%`;
  const formatHours = (value) => value === null ? '--' : `${value} 小时`;
  const formatQuestions = (value) => value === null ? '累计练习待接入' : `累计练习 ${value} 题`;

  return (
    <section className="practice-overview" aria-labelledby="practice-overview-title">
      <header className="practice-overview__banner">
        <div className="practice-overview__hero-copy">
          <span className="practice-overview__greeting"><span>准备开始今天的练习</span> <span aria-hidden="true">🌿</span></span>
          <h1 id="practice-overview-title">练习工坊</h1>
          <p>今日建议完成 <strong>{stats.todayGoal}</strong> 道综合题，预计 <strong>15</strong> 分钟</p>
          <div className="practice-overview__hero-actions">
            <button type="button" className="practice-overview__primary-action" onClick={() => onOpenModule(PRIMARY_TRAINING_GROUPS[0].cards[0])}>
              <CirclePlay aria-hidden="true" size={18} />开始今日练习
            </button>
            <button type="button" className="practice-overview__secondary-action" onClick={() => onOpenModule(recentCard)}>
              <Clock3 aria-hidden="true" size={17} />继续上次练习
            </button>
          </div>
        </div>
        <div className="practice-overview__hero-summary" role="region" aria-label="学习概览">
          <SummaryMetric
            icon={CalendarDays}
            label="近 30 天练习"
            value={stats.windowPracticeCount === null ? '--' : `${stats.windowPracticeCount} 题`}
            hint="正式审核完成题目"
          />
          <SummaryMetric
            icon={Target}
            label="平均正确率"
            value={formatPercent(stats.averageAccuracy)}
            hint={stats.averageAccuracy === null ? '暂无数据' : '继续保持'}
          />
          <SummaryMetric
            icon={Clock3}
            label="累计学习"
            value={formatHours(stats.totalHours)}
            hint={formatQuestions(stats.totalQuestions)}
            tone="purple"
          />
        </div>
        <TrainingBannerIllustration />
      </header>

      <div className="practice-overview__layout">
        <section className="practice-overview__main" aria-label="练习模块">
          <div className="practice-overview__training-groups">
            {PRIMARY_TRAINING_GROUPS.map((group) => (
              <section key={group.title} className="practice-overview__training-group" aria-label={group.title}>
                <h2>{group.title}</h2>
                <div className="practice-overview__training-grid">
                  {group.cards.map((card) => <TrainingCard key={card.key} card={card} onOpenModule={onOpenModule} />)}
                </div>
              </section>
            ))}
          </div>
        </section>

        <aside className="practice-overview__utilities" aria-label="学习工具">
          <h2 className="practice-overview__utilities-title">其他功能</h2>
          <div className="practice-overview__utility-list">
            {UTILITY_CARDS.map((card) => {
              const Icon = card.icon;
              return (
                <button key={card.key} type="button" className="practice-overview__utility-card" onClick={() => onOpenModule(card)}>
                  <span className="practice-overview__utility-icon"><Icon aria-hidden="true" size={22} /></span>
                  <span><strong>{card.title}</strong><small>{card.description}</small></span>
                  <ChevronRight aria-hidden="true" size={18} />
                </button>
              );
            })}
          </div>
        </aside>
      </div>
    </section>
  );
}
