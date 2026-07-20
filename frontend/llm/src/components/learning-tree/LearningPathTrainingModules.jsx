import React, { useEffect, useState } from 'react';
import { loadTrainingWorkspaceModules } from '../../pageDataLoaders';
import { fetchJsonWithAuthFallback } from '../../utils/api';

export default function LearningPathTrainingModules({ trackId, onNavigate }) {
  const [modules, setModules] = useState([]);

  useEffect(() => {
    let active = true;

    loadTrainingWorkspaceModules({ fetcher: fetchJsonWithAuthFallback }).then((result) => {
      if (!active || result.error) return;
      setModules((result.workspace?.modules || []).filter((module) => module.enabled));
    });

    return () => { active = false; };
  }, []);

  if (modules.length === 0) return null;

  return (
    <nav data-testid="learning-path-training-modules" className="learning-path-training-modules" aria-label="训练工坊模块">
      <span>训练工坊</span>
      <div>
        {modules.map((module) => (
          <button
            key={module.key}
            type="button"
            onClick={() => onNavigate?.({
              page: 'practice',
              params: { view: 'workspace', taskType: module.key, ...(trackId ? { trackId } : {}) },
            })}
          >
            <strong>{module.label}</strong>
            <small>{module.description}</small>
          </button>
        ))}
      </div>
    </nav>
  );
}
