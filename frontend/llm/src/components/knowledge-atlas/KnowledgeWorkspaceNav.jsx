import React from 'react';

export default function KnowledgeWorkspaceNav({
  activeWorkspace,
  onSelect,
  className = '',
}) {
  return (
    <nav className={`knowledge-page__workspace-nav ${className}`.trim()} aria-label="知识库内容">
      <button
        type="button"
        className={activeWorkspace === 'sources' ? 'is-active' : ''}
        onClick={() => onSelect('sources')}
      >
        知识资料与个性化数据
      </button>
      <button type="button" className={activeWorkspace === 'questions' ? 'is-active' : ''} onClick={() => onSelect('questions')}>
        题目数据
      </button>
    </nav>
  );
}
