import React, { useEffect, useState } from 'react';
import { fetchWithAuth } from '../utils/api';

const sections = { attempts: '历史答题', mistakes: '历史错题', mastery: '历史掌握记录', plans: '历史学习计划', tasks: '历史学习任务', daily_plans: '每日任务', daily: '每日任务条目', focus: '专注记录', activities: '学习活动', sessions: '历史会话', interventions: '学习干预', events: '智能体记录' };
const labels = { id: '记录编号', question_id: '题目编号', answer: '答案', is_correct: '是否正确', score: '得分', feedback: '反馈', created_at: '记录时间', kp_id: '知识点编号', mastery: '掌握度', confidence: '可信度', wrong_count: '错题次数', review_count: '复习次数', last_review_at: '上次复习', next_review_at: '下次复习', title: '标题', summary: '摘要', status: '状态', source: '来源', task_id: '任务编号', task_type: '任务类型', task_content: '任务内容', estimated_minutes: '预计分钟', completed_at: '完成时间', host_task_id: '每日任务编号', task_item_id: '条目编号', item_kind: '类型', active_seconds: '有效秒数', started_at: '开始时间', ended_at: '结束时间', activity_type: '活动类型', resource_id: '资源编号', duration_minutes: '时长（分钟）', completion_status: '完成状态', error_type: '错误类型', action: '措施', reason: '原因', effect_status: '效果状态', agent_name: '智能体', event_type: '事件类型', input_summary: '输入摘要', output_summary: '输出摘要', role: '角色', content: '内容' };

export default function LearningHistoryPanel() {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState('attempts');
  const [sessionId, setSessionId] = useState(null);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    setData(null); setError('');
    const path = sessionId
      ? `/api/learning-activity/history/sessions/${encodeURIComponent(sessionId)}/messages?offset=${offset}&limit=25`
      : `/api/learning-activity/history?section=${section}&offset=${offset}&limit=25`;
    fetchWithAuth(path, { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(`历史记录读取失败（${response.status}）`);
      const result = await response.json();
      if (!controller.signal.aborted) setData(result);
    }).catch((err) => { if (!controller.signal.aborted) setError(err.message); });
    return () => controller.abort();
  }, [open, section, sessionId, offset]);
  return (
    <section className="m-4 rounded-2xl border border-emerald-200 bg-white p-5" aria-label="历史学习记录">
      <button type="button" className="font-semibold text-emerald-800" aria-expanded={open} onClick={() => setOpen(!open)}>历史学习记录（含导入数据）{open ? ' · 收起' : ' · 展开'}</button>
      <p className="mt-2 text-sm text-slate-600">旧版及导入记录与正式批改成绩分开保留；合成演示数据不代表真实学习成果。历史计划不是当前考试的已发布路径，历史会话不混入当前助教对话。</p>
      {open && <>
        <label className="my-4 block">记录类型 <select aria-label="历史记录类型" value={section} onChange={(event) => { setSection(event.target.value); setOffset(0); setSessionId(null); }}>
          {Object.entries(sections).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        {sessionId && <button type="button" onClick={() => { setSessionId(null); setOffset(0); }}>返回历史会话列表</button>}
        {error && <p role="alert">{error}</p>}
        {!data && !error && <p role="status">正在加载历史记录…</p>}
        {data && <>
          <p role="status">{sessionId ? '会话消息' : sections[section]}：共 {data.total} 条</p>
          {data.items.length === 0 && <p>暂无此类历史记录。</p>}
          <div className="my-3 space-y-3">{data.items.map((item) => <article key={item.id} className="rounded-xl border border-slate-200 p-4">
            <dl className="space-y-1">{Object.entries(item).filter(([, value]) => value !== null && value !== '').map(([key, value]) => <div key={key} className="break-words whitespace-pre-wrap"><dt className="inline font-medium">{labels[key] || extraLabels[key] || key}：</dt><dd className="inline">{typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</dd></div>)}</dl>
            {section === 'sessions' && !sessionId && <button type="button" className="mt-2 text-emerald-700" onClick={() => { setSessionId(item.id); setOffset(0); }}>查看会话内容</button>}
          </article>)}</div>
          <div className="flex gap-4"><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</button><span>第 {Math.floor(offset / 25) + 1} 页</span><button type="button" disabled={!data.has_more} onClick={() => setOffset(offset + 25)}>下一页</button></div>
        </>}
      </>}
    </section>
  );
}

const extraLabels = { plan_id: '计划编号', target_kp_ids: '目标知识点编号', daily_available_minutes: '每日可用分钟', kp_ids_json: '关联知识点编号', resource_ids_json: '关联资源编号', due_at: '截止时间', focus_session_id: '专注记录编号', resource_type: '资源类型', ordinal: '顺序', required_question_count: '要求题数' };