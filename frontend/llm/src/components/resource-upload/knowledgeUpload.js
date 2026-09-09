import { API_BASE, MAIN_API_BASE, fetchWithAuth } from '../../utils/api';

// Share the existing business adapters; file extensions select a supported parser,
// never infer whether the user wants a textbook, questions or a syllabus.
export async function uploadKnowledgeFiles(files, { scope = 'personal', onStage = () => {} } = {}) {
  const contentFiles = scope === 'personal'
    ? files.filter(file => /\.(pdf|md|txt)$/i.test(file.name || '')) : [];
  const legacyFiles = files.filter(file => !contentFiles.includes(file));
  let chapterCount = 0;
  for (const file of contentFiles) {
    onStage(`正在解析《${file.name}》并生成章节、切片和知识点...`);
    const params = new URLSearchParams({
      filename: file.name, title: file.name.replace(/\.[^.]+$/, '') || '用户教材', apply: 'true',
    });
    const response = await fetchWithAuth(`${MAIN_API_BASE}/knowledge/content/import-file?${params}`, {
      method: 'POST', body: file,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === 'string'
      ? data.detail : data.detail?.message || data.error || '资料导入失败');
    if (data.chapter_hierarchy?.ok !== true) throw new Error('资料已处理，但后端没有生成章节层级数据；请先检查个人知识库。');
    chapterCount += Number(data.chapter_hierarchy.chapter_nodes || 0);
  }
  if (legacyFiles.length) {
    const body = new FormData();
    legacyFiles.forEach(file => body.append('files', file));
    const response = await fetchWithAuth(`${API_BASE}/knowledge/upload?scope=${scope}`, { method: 'POST', body });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || '上传失败');
    }
    onStage('已接收旧格式资料，准备构建向量索引...');
  }
  return { chapterCount, contentCount: contentFiles.length, legacyPending: legacyFiles.length > 0 };
}