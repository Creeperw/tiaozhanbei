import React, { useEffect, useRef, useState } from 'react';
import PersonalizationPage from './PersonalizationPage';
import LearningInsightsReportPage from './LearningInsightsReportPage';
import ReviewDashboardPanel from './ReviewDashboardPanel';
import ResourceUploadPage from './ResourceUploadPage';

const validTaskKeys = new Set(['user-profile', 'reports', 'review', 'memory', 'resources']);
const normalizeTask = (value) => (
  validTaskKeys.has(value) ? value : 'reports'
);

export default function PersonalizationHubPage({ navigationContext = {}, onNavigate, currentUser }) {
  const activeTab = normalizeTask(navigationContext.view);
  const [renderedTab, setRenderedTab] = useState(activeTab);
  const [transitionPhase, setTransitionPhase] = useState('idle');
  const timersRef = useRef([]);
  const displayName = String(
    currentUser?.display_name || currentUser?.username || currentUser || '用户',
  ).trim() || '用户';

  useEffect(() => {
    timersRef.current.forEach((timer) => window.clearTimeout(timer));
    timersRef.current = [];
    if (activeTab === renderedTab) {
      setTransitionPhase('idle');
      return undefined;
    }
    setTransitionPhase('exiting');
    timersRef.current.push(window.setTimeout(() => {
      setRenderedTab(activeTab);
      setTransitionPhase('entering');
      timersRef.current.push(window.setTimeout(() => setTransitionPhase('idle'), 110));
    }, 85));
    return () => timersRef.current.forEach((timer) => window.clearTimeout(timer));
  }, [activeTab]);

  const tabs = [
    { key: 'reports', label: `${displayName}的学情报告` },
    { key: 'user-profile', label: '学习画像' },
    { key: 'review', label: '复习与掌握' },
    { key: 'memory', label: '学习记忆' },
    { key: 'resources', label: '上传资源' },
  ];

  return (
    <div className="personalization-hub">
      <nav className="personalization-hub__section-nav" aria-label="个人数据页面切换">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            aria-current={activeTab === tab.key ? 'page' : undefined}
            onClick={() => onNavigate?.({ page: 'personalization', params: { view: tab.key } })}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main
        className={`personalization-hub__task${renderedTab === 'user-profile' ? ' personalization-hub__task--profile' : ''}`}
        data-phase={transitionPhase}
        aria-live="polite"
      >
        {renderedTab === 'user-profile' && <PersonalizationPage onBackHome={null} embedded view="user-profile" />}
        {renderedTab === 'reports' && <LearningInsightsReportPage onNavigate={onNavigate} currentUser={currentUser} />}
        {renderedTab === 'review' && <ReviewDashboardPanel />}
        {renderedTab === 'memory' && <PersonalizationPage onBackHome={null} embedded view="memory" />}
        {renderedTab === 'resources' && <ResourceUploadPage initialType={navigationContext.uploadType} onNavigate={onNavigate} />}
      </main>
    </div>
  );
}
