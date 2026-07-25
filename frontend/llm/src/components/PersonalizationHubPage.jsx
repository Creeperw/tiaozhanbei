import React, { useEffect, useState } from 'react';
import PersonalizationPage from './PersonalizationPage';
import ReportsPage from './ReportsPage';
import ReviewDashboardPanel from './ReviewDashboardPanel';

const tabs = [
  { key: 'user-profile', label: '用户画像' },
  { key: 'reports', label: '学情报告' },
  { key: 'review', label: '复习与掌握' },
];

const validTaskKeys = new Set(tabs.map((tab) => tab.key));
const normalizeTask = (value) => (
  validTaskKeys.has(value) ? value : 'user-profile'
);

export default function PersonalizationHubPage({ navigationContext = {}, onNavigate }) {
  const [activeTab, setActiveTab] = useState(() => normalizeTask(navigationContext.view));

  useEffect(() => {
    setActiveTab(normalizeTask(navigationContext.view));
  }, [navigationContext.view]);

  const selectTask = (task) => {
    setActiveTab(task);
    onNavigate?.({ page: 'personalization', params: { view: task } });
  };

  return (
    <div className="personalization-hub">
      <nav className="personalization-hub__tabs" aria-label="个性数据二级菜单">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            aria-current={activeTab === tab.key ? 'page' : undefined}
            onClick={() => selectTask(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <main className="personalization-hub__task" aria-live="polite">
        {activeTab === 'user-profile' && <PersonalizationPage onBackHome={null} embedded view="user-profile" />}
        {activeTab === 'reports' && <ReportsPage />}
        {activeTab === 'review' && <ReviewDashboardPanel />}
      </main>
    </div>
  );
}
