import React from 'react';

export default function KnowledgeWorkspaceNav({
  atlasEnabled = true,
  activeWorkspace,
  activeScope,
  onSelect,
  className = '',
}) {
  return (
    <nav className={`knowledge-page__workspace-nav ${className}`.trim()} aria-label="知识库内容">
      {atlasEnabled && (
        <button type="button" className={activeWorkspace === 'atlas' ? 'is-active' : ''} onClick={() => onSelect('atlas')}>
          知识星球
        </button>
      )}
      <button
        type="button"
        className={activeWorkspace === 'sources' && activeScope !== 'personal' ? 'is-active' : ''}
        onClick={() => onSelect('sources', 'public')}
      >
        知识资料
      </button>
      <button
        type="button"
        className={activeWorkspace === 'sources' && activeScope === 'personal' ? 'is-active' : ''}
        onClick={() => onSelect('sources', 'personal')}
      >
        个性化数据
      </button>
      <button type="button" className={activeWorkspace === 'questions' ? 'is-active' : ''} onClick={() => onSelect('questions')}>
        题目数据
      </button>
    </nav>
  );
}
