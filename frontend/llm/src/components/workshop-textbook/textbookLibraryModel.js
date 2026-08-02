import { normalizeBookName } from '../learningPlanDashboard';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
import { textbookPlanningLabel } from './textbookPlanning';

export const TEXTBOOK_FILTERS = [
  { id: 'all', label: '全部教材' },
  { id: 'learning', label: '学习中' },
  { id: 'not-started', label: '未开始' },
  { id: 'completed', label: '已完成' },
  { id: 'planned', label: '已加入计划' },
];

function textbookName(item) {
  return normalizeBookName(item?.navigation?.book || item?.book || item?.name || item?.title);
}

function finiteCount(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

export function buildTextbookViewModels({
  textbooks = [],
  plannedBooks = [],
  snapshotsByBook = {},
  currentBookName = '',
} = {}) {
  const plannedNames = new Set(plannedBooks.map(textbookName).filter(Boolean));
  const normalizedCurrentBook = normalizeBookName(currentBookName);

  return textbooks.map((textbook) => {
    const name = textbookName(textbook);
    const snapshot = snapshotsByBook[name];
    const isCurrent = Boolean(name && name === normalizedCurrentBook);
    const progress = Number.isFinite(snapshot?.progress) ? snapshot.progress : null;
    const progressAvailable = progress !== null && !snapshot?.unavailable;
    const hasStarted = isCurrent || finiteCount(snapshot?.completedSections) > 0
      || Boolean(snapshot?.lastSectionId) || finiteCount(snapshot?.lastActivityAt) > 0;
    const isCompleted = progressAvailable && progress >= 1;
    const statusKnown = isCurrent || progressAvailable;

    return {
      id: textbook.id || textbook.node_id || `${textbook.navigation?.route_id || 'textbook_14_5'}:${name}`,
      name,
      routeId: textbook.navigation?.route_id || snapshot?.route || 'textbook_14_5',
      categoryLabel: textbookPlanningLabel(name) || textbook.stage_title || '专业教材',
      coverUrl: textbookCoverUrl(name),
      description: textbookIntroduction(name),
      chapterCount: finiteCount(textbook.children_count ?? textbook.child_count),
      knowledgePointCount: finiteCount(textbook.count),
      totalSectionCount: finiteCount(snapshot?.totalSections),
      completedSectionCount: finiteCount(snapshot?.completedSections),
      progress,
      progressAvailable,
      statusKnown,
      hasStarted,
      isCompleted,
      isCurrent,
      isPlanned: plannedNames.has(name),
      origin: textbook.origin || 'platform',
      lastSectionId: snapshot?.lastSectionId || '',
      lastActivityAt: finiteCount(snapshot?.lastActivityAt),
      source: textbook,
    };
  });
}

export function textbookMatchesFilter(textbook, filter) {
  if (filter === 'learning') return textbook.statusKnown && textbook.hasStarted && !textbook.isCompleted;
  if (filter === 'not-started') return textbook.statusKnown && !textbook.hasStarted && !textbook.isCompleted;
  if (filter === 'completed') return textbook.statusKnown && textbook.isCompleted;
  if (filter === 'planned') return textbook.isPlanned;
  return true;
}

export function textbookMatchesSource(textbook, source) {
  if (!source || source === 'all') return true;
  if (source === 'uploaded') return textbook.origin === 'user_upload';
  if (source === 'platform') return textbook.origin !== 'user_upload';
  return true;
}

export function filterTextbookViewModels(textbooks, { filter = 'all', source = 'all', query = '' } = {}) {
  const normalizedQuery = String(query).trim().toLocaleLowerCase('zh-CN');
  return textbooks.filter((textbook) => {
    if (!textbookMatchesFilter(textbook, filter)) return false;
    if (!textbookMatchesSource(textbook, source)) return false;
    if (!normalizedQuery) return true;
    return [textbook.name, textbook.categoryLabel, textbook.description]
      .some((value) => String(value || '').toLocaleLowerCase('zh-CN').includes(normalizedQuery));
  });
}

export function textbookSourceCounts(textbooks) {
  return {
    all: textbooks.length,
    uploaded: textbooks.filter((book) => book.origin === 'user_upload').length,
    platform: textbooks.filter((book) => book.origin !== 'user_upload').length,
  };
}

export function textbookFilterCounts(textbooks) {
  const statusCount = (filter) => textbooks.filter((book) => textbookMatchesFilter(book, filter)).length;
  return {
    all: textbooks.length,
    learning: statusCount('learning'),
    'not-started': statusCount('not-started'),
    completed: statusCount('completed'),
    planned: textbooks.filter((book) => book.isPlanned).length,
  };
}
