export const getKnowledgeScopeNotice = (scope, isAdmin) => {
  if (scope === 'personal') {
    return '个人知识仅当前用户可见、可检索；上传资料不会进入公共库。';
  }

  return isAdmin
    ? '公共知识面向所有用户可检索，由管理员负责维护与更新。'
    : '公共知识库由管理员维护。你可以查看并检索公共知识，但不能上传、删除或重建公共文档。';
};

export const getSearchFeedback = ({ isSearching, error, hasQueried, resultCount }) => {
  if (isSearching) return { tone: 'loading', text: '正在检索公共与个人知识…' };
  if (error) return { tone: 'error', text: error };
  if (hasQueried && resultCount === 0) return { tone: 'empty', text: '未找到相关内容' };
  return null;
};
