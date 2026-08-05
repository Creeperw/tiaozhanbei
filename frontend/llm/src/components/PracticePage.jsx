import React, { useEffect, useState } from 'react';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import TrainingOverview from './practice/TrainingOverview';
import TrainingWorkspace from './practice/TrainingWorkspace';
import {
  DEFAULT_TRAINING_OVERVIEW_STATS,
  buildTrainingOverviewStats,
  normalizeTaskIntent,
} from './practice/taskRegistry';

export default function PracticePage({
  navigationContext = {},
  overviewStats,
  onNavigate,
}) {
  // 受控组件：当前模块/视图完全由 navigationContext（即 App 的 pageIntent）派生，
  // 不持有本地视图 state，popstate 恢复或 URL 直访时都能正确渲染。
  const rawTaskType = navigationContext.taskType;
  const taskType = normalizeTaskIntent(rawTaskType).taskType;
  const initialMode = navigationContext.initialMode || taskType;
  const showWorkspace = Boolean(rawTaskType) || navigationContext.view === 'workspace';
  const [loadedOverviewStats, setLoadedOverviewStats] = useState(DEFAULT_TRAINING_OVERVIEW_STATS);

  useEffect(() => {
    if (overviewStats !== undefined) return undefined;
    let active = true;
    const requestOverview = (path) => fetchJsonWithAuthFallback({
      paths: [path],
      fallback: {},
    }).then((result) => result.data).catch(() => ({}));

    fetchJsonWithAuthFallback({
      paths: ['/v1/checkin'],
      fallback: {},
      options: { method: 'POST', body: '{}' },
    }).catch(() => {});

    Promise.all([
      requestOverview('/v1/learning-statistics/overview?days=30'),
      requestOverview('/v1/learning-activity/summary?days=7&recent_limit=100'),
    ]).then(([statistics, activitySummary]) => {
      if (active) setLoadedOverviewStats(buildTrainingOverviewStats(statistics, activitySummary));
    });

    return () => { active = false; };
  }, [overviewStats]);

  useEffect(() => {
    const request = async (path, body) => {
      const result = await fetchJsonWithAuthFallback({
        paths: [path],
        options: {
          method: 'POST',
          keepalive: true,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      });
      return result.data;
    };
    const tracker = createLearningFocusTracker({
      request,
      resourceType: 'training_workspace',
      resourceId: 'practice',
    });
    tracker.start().catch(() => {});
    return () => { tracker.stop().catch(() => {}); };
  }, []);

  const openWorkshopModule = ({ key, initialMode }) => {
    // 模块切换通过 App 导航完成：更新 pageIntent 并同步 URL（/practice/<slug>），
    // App 端会递增 navigationRevision 触发本组件重挂载，用新 navigationContext 初始化。
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: key,
        initialMode: initialMode || key,
      },
    });
  };

  if (!showWorkspace) {
    return (
      <TrainingOverview
        onOpenModule={openWorkshopModule}
        overviewStats={overviewStats ?? loadedOverviewStats}
      />
    );
  }

  return (
    <TrainingWorkspace
      navigationContext={{
        ...navigationContext,
        taskType,
        initialMode,
        view: 'workspace',
      }}
      onNavigate={onNavigate}
      onBack={() => onNavigate?.({ page: 'training-workshop', params: {} })}
    />
  );
}
