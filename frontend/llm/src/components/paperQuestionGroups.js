export const paperQuestionGroups = [
  { key: 'single_choice', label: '单选题', types: ['single_choice', '单选题', '单项选择题'] },
  { key: 'multiple_choice', label: '多选题', types: ['multiple_choice', '多选题', '多项选择题'] },
  { key: 'fill_blank', label: '填空题', types: ['fill_blank', '填空题'] },
  { key: 'short_answer', label: '简答题', types: ['short_answer', '简答题', 'case_quiz', '案例题'] },
];

export function groupPaperItems(items = []) {
  const groups = paperQuestionGroups.map((group) => ({ ...group, items: [] }));
  const fallback = groups.at(-1);
  items.forEach((item) => {
    const group = groups.find((candidate) => candidate.types.includes(item?.question_type)) || fallback;
    group.items.push(item);
  });
  return groups.filter((group) => group.items.length > 0);
}
