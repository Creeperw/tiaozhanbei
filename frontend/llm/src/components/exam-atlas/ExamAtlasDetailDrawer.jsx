import React, { useEffect, useState } from 'react';
import { BookOpen, Brain, ClipboardCheck } from 'lucide-react';
import { Button, Drawer, EmptyState, Skeleton, StatusBadge } from '../ui';
import { loadLearnerKnowledgePointState } from './examAtlasApi';
import {
  buildAssistantIntent,
  buildKnowledgeIntent,
  buildPracticeIntent,
} from './examAtlasModel';

export default function ExamAtlasDetailDrawer({
  concept,
  membershipId,
  onClose,
  onNavigate,
  trackId,
}) {
  const primary = concept?.variants?.[0];
  const [learnerStateRequest, setLearnerStateRequest] = useState({
    kpId: null,
    status: 'idle',
    data: null,
  });
  const learnerStateStatus = primary?.kp_id === learnerStateRequest.kpId
    ? learnerStateRequest.status
    : 'loading';
  const learnerState = primary?.kp_id === learnerStateRequest.kpId
    ? learnerStateRequest.data
    : null;
  const context = primary ? {
    trackId,
    membershipId,
    kpId: primary.kp_id,
    kpName: concept.name,
    path: primary.path || concept.path || [],
  } : null;

  useEffect(() => {
    let cancelled = false;
    const kpId = primary?.kp_id;
    if (!kpId) return () => { cancelled = true; };
    loadLearnerKnowledgePointState(kpId)
      .then((payload) => {
        if (!cancelled) {
          setLearnerStateRequest({ kpId, status: 'ready', data: payload });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLearnerStateRequest({ kpId, status: 'error', data: null });
        }
      });
    return () => { cancelled = true; };
  }, [primary?.kp_id]);

  return (
    <Drawer open={Boolean(concept)} title={concept?.name || '知识点详情'} onClose={onClose}>
      {context ? (
        <div className="space-y-6">
          <div className="space-y-3">
            <StatusBadge status="success">已确认考纲映射</StatusBadge>
            <p className="text-sm leading-6 text-slate-600">
              {(context.path || []).join(' / ') || concept.name}
            </p>
          </div>

          <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200">
            <div className="bg-white p-4">
              <dt className="text-xs text-slate-500">公共知识点记录</dt>
              <dd className="mt-1 text-lg font-semibold text-slate-950">{concept.variants.length}</dd>
            </div>
            <div className="bg-white p-4">
              <dt className="text-xs text-slate-500">已确认关联</dt>
              <dd className="mt-1 text-lg font-semibold text-emerald-700">{concept.acceptedCount}</dd>
            </div>
          </dl>

          <section aria-labelledby="atlas-learning-state-title">
            <h3 id="atlas-learning-state-title" className="text-sm font-semibold text-slate-950">我的学习状态</h3>
            {learnerStateStatus === 'loading' && <Skeleton label="正在加载学习状态" lines={2} />}
            {learnerStateStatus === 'error' && (
              <p className="mt-3 text-sm text-slate-600">学习状态暂不可用，不影响查看知识点和继续学习。</p>
            )}
            {learnerStateStatus === 'ready' && learnerState && (
              <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200">
                <div className="bg-white p-4">
                  <dt className="text-xs text-slate-500">掌握度</dt>
                  <dd className="mt-1 text-lg font-semibold text-slate-950">
                    {learnerState.mastery_score == null ? '尚未评估' : `${learnerState.mastery_score}%`}
                  </dd>
                </div>
                <div className="bg-white p-4">
                  <dt className="text-xs text-slate-500">练习次数</dt>
                  <dd className="mt-1 text-lg font-semibold text-slate-950">{learnerState.attempt_count}</dd>
                </div>
                <div className="bg-white p-4">
                  <dt className="text-xs text-slate-500">复习状态</dt>
                  <dd className="mt-1 text-sm font-semibold text-slate-950">
                    {learnerState.review_due ? '复习已到期' : '暂不需要复习'}
                  </dd>
                </div>
                <div className="bg-white p-4">
                  <dt className="text-xs text-slate-500">错题复盘</dt>
                  <dd className="mt-1 text-sm font-semibold text-slate-950">
                    {learnerState.active_mistake_count} 道待复盘错题
                  </dd>
                </div>
              </dl>
            )}
          </section>

          <section aria-labelledby="atlas-kp-variants-title">
            <h3 id="atlas-kp-variants-title" className="text-sm font-semibold text-slate-950">知识点标识</h3>
            <ul className="mt-3 divide-y divide-slate-100 border-y border-slate-100">
              {concept.variants.map((variant) => (
                <li key={variant.kp_id} className="py-3">
                  <div className="font-mono text-xs text-slate-500">{variant.kp_id}</div>
                  <div className="mt-1 text-sm text-slate-800">{(variant.path || []).join(' / ')}</div>
                </li>
              ))}
            </ul>
          </section>

          <div className="grid gap-2 sm:grid-cols-3">
            <Button onClick={() => onNavigate(buildPracticeIntent(context))}>
              <ClipboardCheck aria-hidden="true" size={16} />
              开始练习
            </Button>
            <Button variant="secondary" onClick={() => onNavigate(buildKnowledgeIntent(context))}>
              <BookOpen aria-hidden="true" size={16} />
              查看资料
            </Button>
            <Button variant="secondary" onClick={() => onNavigate(buildAssistantIntent(context))}>
              <Brain aria-hidden="true" size={16} />
              询问助教
            </Button>
          </div>

          <p className="text-xs leading-5 text-slate-500">
            掌握度来自真实练习写回；考纲映射本身不会被用来推断学习进度。
          </p>
        </div>
      ) : (
        <EmptyState title="暂无知识点详情" description="该节点没有可展示的已确认公共知识点。" />
      )}
    </Drawer>
  );
}
