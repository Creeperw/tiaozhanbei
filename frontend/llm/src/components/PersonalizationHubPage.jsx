import React, { useEffect, useState } from 'react';
import PersonalizationPage from './PersonalizationPage';
import PlanningPage from './PlanningPage';
import ReportsPage from './ReportsPage';
import OnboardingSurveyPanel from './OnboardingSurveyPanel';
import ProfileConflictList from './ProfileConflictList';
import ReviewDashboardPanel from './ReviewDashboardPanel';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const tabs = [
  { key: 'profile', label: '学习画像' },
  { key: 'memory', label: '学习记忆' },
  { key: 'planning', label: '学情规划' },
  { key: 'reports', label: '学情报告' },
  { key: 'review', label: '复习与掌握' },
  { key: 'survey', label: '学情调查' },
  { key: 'conflicts', label: '冲突清单' },
];

const visualByTab = {
  survey: {
    title: '个性化数据库',
    description: '把学情调查、偏好、时间约束和目标方向沉淀为学情智能体可消费的 L0 输入。',
  },
  profile: {
    title: '学习画像',
    description: '维护学习目标、时间约束、资源偏好与当前薄弱点，为推荐和助教提供稳定依据。',
  },
  memory: {
    title: '学习记忆',
    description: '集中管理手动记录、智能抽取候选和过期信息，不与画像编辑混在同一屏。',
  },
  planning: {
    title: '学情规划',
    description: '把长期阶段、短期任务和今日执行窗口串成可追踪的学习路径。',
  },
  reports: {
    title: '学情报告',
    description: '用能力雷达、路径进度和错因归因呈现最近学习状态。',
  },
  review: {
    title: '复习与掌握',
    description: '查看已进入复习队列的知识点、当前掌握度、复习状态和历史变化。',
  },
  conflicts: {
    title: '冲突确认',
    description: '把行为证据和用户确认分开处理，避免自动覆盖稳定画像。',
  },
};

const validTaskKeys = new Set(tabs.map((tab) => tab.key));
const normalizeTask = (value) => (validTaskKeys.has(value) ? value : 'profile');

export default function PersonalizationHubPage({ navigationContext = {}, onNavigate }) {
  const [activeTab, setActiveTab] = useState(() => normalizeTask(navigationContext.view));
  const [memories, setMemories] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [conflictError, setConflictError] = useState('');

  const loadConflicts = async () => {
    setConflictError('');
    try {
      const [memoryRes, candidateRes] = await Promise.all([
        fetchWithAuth(`${API_BASE}/personalization/memories`),
        fetchWithAuth(`${API_BASE}/personalization/candidates?status=pending`),
      ]);
      if (!memoryRes.ok || !candidateRes.ok) {
        const errorPayload = await readJsonResponse(!memoryRes.ok ? memoryRes : candidateRes, {});
        throw new Error(errorPayload.detail || '冲突清单加载失败');
      }
      const memoryData = await readJsonResponse(memoryRes, []);
      const candidateData = await readJsonResponse(candidateRes, []);
      setMemories(Array.isArray(memoryData) ? memoryData : []);
      setCandidates(Array.isArray(candidateData) ? candidateData : []);
    } catch (e) {
      setConflictError(e.message || '冲突清单加载失败');
    }
  };

  useEffect(() => {
    if (activeTab === 'conflicts') loadConflicts();
  }, [activeTab]);

  useEffect(() => {
    setActiveTab(normalizeTask(navigationContext.view));
  }, [navigationContext.view]);

  const selectTask = (task) => {
    setActiveTab(task);
    onNavigate?.({ page: 'personalization', params: { view: task } });
  };

  return (
    <div className="personalization-hub">
      <header className="personalization-hub__header">
        <div>
          <span className="app-shell__section-label">画像 · 记忆 · 规划 · 报告</span>
          <h2>{visualByTab[activeTab].title}</h2>
          <p>{visualByTab[activeTab].description}</p>
        </div>
        <nav className="personalization-hub__tabs" aria-label="个性化任务">
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
      </header>
      <main className="personalization-hub__task" aria-live="polite">
        {activeTab === 'survey' && <OnboardingSurveyPanel />}
        {['profile', 'memory'].includes(activeTab) && <PersonalizationPage onBackHome={null} embedded view={activeTab} />}
        {activeTab === 'planning' && <PlanningPage />}
        {activeTab === 'reports' && <ReportsPage />}
        {activeTab === 'review' && <ReviewDashboardPanel />}
        {activeTab === 'conflicts' && (
          <>
            {conflictError && <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{conflictError}</div>}
            <ProfileConflictList memories={memories} candidates={candidates} />
          </>
        )}
      </main>
    </div>
  );
}
