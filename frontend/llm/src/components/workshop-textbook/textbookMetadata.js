const BOOK_INTRODUCTIONS = {
  中医学基础: '系统讲解阴阳五行、藏象、气血津液、经络与病因病机等核心理论，帮助学习者建立完整、清晰的中医学思维框架。',
  中医学概论: '从中医学理论、诊法、辨证与防治原则出发，循序认识中医知识体系，为后续专业课程学习奠定基础。',
  中药学: '围绕中药性能、功效、应用与配伍规律展开，结合代表药物构建辨药、用药和综合分析能力。',
  方剂学: '以治法与方剂配伍为主线，学习经典方剂的组成、功用和临床运用，形成由法统方、据证用方的思维。',
  针灸学: '系统学习经络腧穴、刺灸方法和临床治疗规律，将基础理论、操作技能与常见病证应用有机结合。',
};

const COVER_NAME_ALIASES = {
  中药分析: '中药分析学',
  细胞生物学实验: '细胞生物学基础',
};

// TreeKG 新版知识图谱（带左侧目录）已生成数据的教材。
// 数据目录: TreeKG-main/src/data/{教材}/
const TREEKG_BOOKS = ['中医学基础', '中医学基础_clean', '中医文化学'];

export function textbookKnowledgeGraphUrl(book) {
  const name = String(book || '').replace(/[《》]/g, '').trim();
  const baseUrl = String(import.meta.env.BASE_URL || '/').replace(/\/$/, '');
  // 优先新版 TreeKG viewer（带目录树 + 教材切换）
  if (TREEKG_BOOKS.includes(name)) {
    return `${baseUrl}/treekg/?book=${encodeURIComponent(name)}`;
  }
  return '';
}

export function textbookIntroduction(book) {
  const name = String(book || '本教材').replace(/[《》]/g, '').trim();
  if (BOOK_INTRODUCTIONS[name]) return BOOK_INTRODUCTIONS[name];
  if (/护理/.test(name)) return `《${name}》围绕专业护理知识、临床观察与实践能力展开，帮助学习者掌握规范流程，并建立以患者为中心的综合照护思维。`;
  if (/针灸|推拿|骨伤|正骨|筋伤/.test(name)) return `《${name}》注重理论与实践结合，系统梳理基础原理、操作方法和临床应用，帮助学习者形成规范、连贯的专业技能体系。`;
  if (/中药|药学|药理|药事|本草|药用植物/.test(name)) return `《${name}》系统梳理相关基础理论、核心知识与实践应用，帮助学习者建立准确的专业认知和分析、应用能力。`;
  if (/实验/.test(name)) return `《${name}》以实验原理、规范操作和结果分析为主线，培养严谨的实践习惯，并加深对相关理论知识的理解。`;
  return `《${name}》按照教材章节系统组织核心概念、重点内容与实践应用，帮助学习者循序建立完整知识结构，提升理解和综合运用能力。`;
}

export function textbookCoverUrl(book) {
  const name = String(book || '').replace(/[《》]/g, '').trim();
  const coverName = COVER_NAME_ALIASES[name] || name;
  return coverName ? `/textbook-covers/${encodeURIComponent(coverName)}.jpg` : '';
}
