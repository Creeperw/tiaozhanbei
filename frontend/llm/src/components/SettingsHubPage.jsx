import React, { useEffect, useState } from 'react';
import ProfileConflictList from './ProfileConflictList';
import LearningGovernancePanel from './LearningGovernancePanel';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { normalizeSettingsView } from '../settingsNavigation';

const tabs = [
  { key: 'governance', label: '干预与通知' },
  { key: 'conflicts', label: '冲突清单' },
];

export default function SettingsHubPage({ navigationContext = {}, onNavigate }) {
  const [activeTab, setActiveTab] = useState(() => normalizeSettingsView(navigationContext.view));
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
    } catch (error) {
      setConflictError(error.message || '冲突清单加载失败');
    }
  };

  useEffect(() => {
    if (activeTab === 'conflicts') loadConflicts();
  }, [activeTab]);

  useEffect(() => {
    setActiveTab(normalizeSettingsView(navigationContext.view));
  }, [navigationContext.view]);

  const selectTask = (task) => {
    setActiveTab(task);
    onNavigate?.({ page: 'settings', params: { view: task } });
  };

  return (
    <div className="settings-hub">
      <nav className="personalization-hub__section-nav" aria-label="系统通知页面切换">
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
      <main className="settings-hub__task" aria-live="polite">
        {activeTab === 'governance' && <LearningGovernancePanel focusNotificationId={navigationContext.notificationId} />}
        {activeTab === 'conflicts' && (
          <>
            {conflictError && <div className="mb-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{conflictError}</div>}
            <ProfileConflictList memories={memories} candidates={candidates} onRefresh={loadConflicts} />
          </>
        )}
      </main>
    </div>
  );
}
