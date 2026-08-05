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
  const initialTaskIntent = normalizeTaskIntent(navigationContext.taskType);
  const [activeTaskType, setActiveTaskType] = useState(() => initialTaskIntent.taskType);
  const [activeInitialMode, setActiveInitialMode] = useState(() => initialTaskIntent.initialMode);
  const [view, setView] = useState(() => (
    navigationContext.taskType || navigationContext.view === 'workspace' ? 'workspace' : 'overview'
  ));
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
    setActiveTaskType(key);
    setActiveInitialMode(initialMode || key);
    setView('workspace');
  };

  if (view === 'overview') {
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
        taskType: activeTaskType,
        initialMode: activeInitialMode,
        view: 'workspace',
      }}
      onNavigate={onNavigate}
      onBack={() => setView('overview')}
    />
  );
}
