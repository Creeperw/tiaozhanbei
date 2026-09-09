import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import ResourceUploadPage from '../ResourceUploadPage';
import KnowledgeUploadForm from './KnowledgeUploadForm';
import TextbookUploadHistory from './TextbookUploadHistory';
import UploadProgress from './UploadProgress';
import { uploadIntent } from './uploadNavigation';
import { uploadKnowledgeFiles } from './knowledgeUpload';
import { fetchWithAuth } from '../../utils/api';
import { loadTextbookImports, loadTextbookImportStatus, uploadTextbook, invalidateTextbookPdfCatalogCache } from '../workshop-textbook/textbookPdfApi';
import { readTeachingResourcesPageCache, updateTeachingResourcesPageCache } from '../teachingResourcesPageCache';

vi.mock('../../utils/api', () => ({
  API_BASE: '/api', MAIN_API_BASE: '/api/v1', fetchWithAuth: vi.fn(),
  readJsonResponse: async (response, fallback) => response.json().catch(() => fallback),
}));
vi.mock('../workshop-textbook/textbookPdfApi', () => ({
  loadTextbookCategories: vi.fn(async () => ({ items: ['中医药'] })),
  loadTextbookImports: vi.fn(), loadTextbookImportStatus: vi.fn(), uploadTextbook: vi.fn(),
  invalidateTextbookPdfCatalogCache: vi.fn(),
}));

const response = data => ({ ok: true, json: async () => data });
beforeEach(() => {
  vi.clearAllMocks();
  fetchWithAuth.mockResolvedValue(response({ items: [] }));
  loadTextbookImports.mockResolvedValue({ items: [] });
  loadTextbookImportStatus.mockResolvedValue({ status: 'done', book: { book_id: 'book1', title: '测试教材' } });
  uploadTextbook.mockResolvedValue({ task_id: 'TBI_test', status: 'running' });
});

it('offers four explicit types, rejects unrecognized types and does not upload on selection', () => {
  render(<ResourceUploadPage initialType="some-paper.pdf" />);
  const choices = within(screen.getByRole('region', { name: '选择上传资源类型' }));
  expect(choices.getAllByRole('button')).toHaveLength(4);
  expect(choices.getAllByRole('button').every(button => button.getAttribute('aria-pressed') === 'false')).toBe(true);
  expect(fetchWithAuth).not.toHaveBeenCalled();
  expect(uploadIntent('knowledge')).toEqual({ page: 'personalization', params: { view: 'resources', uploadType: 'knowledge' } });
  expect(uploadIntent('guess.pdf').params.uploadType).toBe('');
});

it('preselects explicit navigation and warns about syllabus activation before any upload', async () => {
  const { rerender } = render(<ResourceUploadPage initialType="knowledge" />);
  expect(screen.getByRole('form', { name: '个人知识资料上传' })).toBeInTheDocument();
  rerender(<ResourceUploadPage initialType="syllabus" />);
  expect(await screen.findByText(/替换当前激活考纲/)).toBeInTheDocument();
  await screen.findByText('还没有个人考纲');
  expect(fetchWithAuth.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
});

it('keeps question confirmation manual and exposes the result destination', async () => {
  const navigate = vi.fn();
  render(<ResourceUploadPage initialType="question" onNavigate={navigate} />);
  expect(screen.getByText(/解析完成不等于已激活/)).toBeInTheDocument();
  await screen.findByText('暂无导入记录');
  fireEvent.click(screen.getByRole('button', { name: '查看个人题库' }));
  expect(navigate).toHaveBeenCalledWith({ page: 'knowledge', params: { view: 'questions' } });
  expect(fetchWithAuth.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true);
});

it('uses the inline textbook form, blocks type switching during work, then invalidates caches and opens the result', async () => {
  let finish;
  uploadTextbook.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  updateTeachingResourcesPageCache('test-owner', { uploadedTextbooks: [] });
  const navigate = vi.fn();
  render(<ResourceUploadPage initialType="textbook" onNavigate={navigate} />);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await screen.findByText('暂无教材上传记录');
  fireEvent.change(screen.getByLabelText(/选择教材 PDF/), { target: { files: [new File(['pdf'], '教材.pdf', { type: 'application/pdf' })] } });
  fireEvent.click(screen.getByRole('button', { name: '开始上传' }));
  const choices = within(screen.getByRole('region', { name: '选择上传资源类型' }));
  await waitFor(() => expect(choices.getAllByRole('button').every(button => button.disabled)).toBe(true));
  await act(async () => finish({ task_id: 'TBI_test', status: 'running' }));
  await screen.findByText('教材处理完成：测试教材');
  expect(uploadTextbook).toHaveBeenCalledTimes(1);
  expect(invalidateTextbookPdfCatalogCache).toHaveBeenCalled();
  expect(readTeachingResourcesPageCache('test-owner')).toBeNull();
  expect(choices.getAllByRole('button').every(button => !button.disabled)).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '阅读教材' }));
  expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ page: 'practice', params: expect.objectContaining({ bookId: 'book1', uploaded: true }) }));
});

it('retries failed status reads without submitting the textbook again', async () => {
  loadTextbookImportStatus.mockRejectedValueOnce(new Error('查询暂时失败'));
  render(<ResourceUploadPage initialType="textbook" />);
  await screen.findByText('暂无教材上传记录');
  fireEvent.change(screen.getByLabelText(/选择教材 PDF/), { target: { files: [new File(['pdf'], '教材.pdf')] } });
  fireEvent.click(screen.getByRole('button', { name: '开始上传' }));
  await screen.findByText('查询暂时失败');
  expect(screen.getByRole('button', { name: '开始上传' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重新查询任务进度' }));
  await screen.findByText('教材处理完成：测试教材');
  expect(uploadTextbook).toHaveBeenCalledTimes(1);
  expect(loadTextbookImportStatus).toHaveBeenCalledTimes(2);
});

it('reads persisted history and exposes no automatic replay for interrupted tasks', async () => {
  const book = { book_id: 'B1', title: '历史教材' };
  loadTextbookImports.mockResolvedValue({ items: [
    { task_id: 'TBI_done', status: 'done', book, progress: { state: 'succeeded' } },
    { task_id: 'TBI_interrupted', status: 'failed', retry_allowed: false, progress: { state: 'failed' }, error: { message: '任务已中断' } },
  ] });
  const open = vi.fn();
  render(<TextbookUploadHistory onOpen={open} />);
  await screen.findByText('历史教材');
  expect(screen.getByText(/结果待核实，请先检查教材书架/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '阅读教材' }));
  expect(open).toHaveBeenCalledWith(book);
  expect(uploadTextbook).not.toHaveBeenCalled();
});

it('shows history errors with manual refresh instead of an empty-success state', async () => {
  loadTextbookImports.mockRejectedValueOnce(new Error('任务列表不可用'));
  render(<TextbookUploadHistory />);
  await screen.findByText('任务列表不可用');
  expect(screen.queryByText('暂无教材上传记录')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新记录' }));
  await screen.findByText('暂无教材上传记录');
});

it('uploads knowledge through the shared adapter once and navigates to personal scope', async () => {
  const navigate = vi.fn();
  fetchWithAuth.mockResolvedValue(response({ chapter_hierarchy: { ok: true, chapter_nodes: 2 } }));
  render(<KnowledgeUploadForm onNavigate={navigate} />);
  const file = new File(['资料'], '知识.md');
  fireEvent.change(screen.getByLabelText('选择知识资料'), { target: { files: [file] } });
  fireEvent.click(screen.getByRole('button', { name: '开始导入个人知识库' }));
  await screen.findByText('资料导入完成，已生成 2 个章节。');
  expect(fetchWithAuth).toHaveBeenCalledTimes(1);
  const [url, options] = fetchWithAuth.mock.calls[0];
  expect(url).toContain('/api/v1/knowledge/content/import-file?');
  expect(options).toEqual({ method: 'POST', body: file });
  fireEvent.click(screen.getByRole('button', { name: '查看个人知识库' }));
  expect(navigate).toHaveBeenCalledWith({ page: 'knowledge', params: { view: 'personal' } });
});

it('preserves legacy knowledge upload and never presents queued indexing as completed', async () => {
  render(<KnowledgeUploadForm />);
  fireEvent.change(screen.getByLabelText('选择知识资料'), { target: { files: [new File(['{}'], '旧资料.json')] } });
  fireEvent.click(screen.getByRole('button', { name: '开始导入个人知识库' }));
  await screen.findByText('文件已接收，旧格式资料的索引状态请到个人知识库查看。');
  expect(fetchWithAuth.mock.calls[0][0]).toBe('/api/knowledge/upload?scope=personal');
  expect(screen.queryByText(/资料导入完成/)).not.toBeInTheDocument();
});

it('keeps public knowledge requests on the existing admin path', async () => {
  const file = new File(['pdf'], '公共.pdf');
  const result = await uploadKnowledgeFiles([file], { scope: 'public' });
  expect(result.legacyPending).toBe(true);
  expect(fetchWithAuth.mock.calls[0][0]).toBe('/api/knowledge/upload?scope=public');
  expect(fetchWithAuth.mock.calls[0][1].body.getAll('files')).toEqual([file]);
});

it('warns that partial or uncertain imports must be checked before resubmission', async () => {
  fetchWithAuth.mockRejectedValue(new Error('连接中断'));
  render(<KnowledgeUploadForm />);
  fireEvent.change(screen.getByLabelText('选择知识资料'), { target: { files: [new File(['text'], '资料.txt')] } });
  fireEvent.click(screen.getByRole('button', { name: '开始导入个人知识库' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('勿直接重复上传');
  expect(screen.getByRole('button', { name: '查看个人知识库' })).toBeEnabled();
});

it('unknown progress does not invent success or percentages', () => {
  render(<UploadProgress progress={{ state: 'new-state' }} />);
  expect(screen.getByRole('status')).toHaveTextContent('状态待核实');
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});