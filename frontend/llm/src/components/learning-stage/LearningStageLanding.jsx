import React, { useEffect, useMemo, useRef, useState } from 'react';
import './LearningStageLanding.css';
import { loadPlannedLearningPath } from '../learning-tree/learningPathApi';
import {
  getStageLayout,
  plannedStagesFromPath,
  STAGE_PALETTE,
} from './learningStageModel';

function snapshotRect(rect) {
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  };
}

function withPresentation(stage, index) {
  return {
    ...stage,
    colors: Array.isArray(stage.colors) && stage.colors.length >= 2
      ? stage.colors
      : STAGE_PALETTE[index % STAGE_PALETTE.length],
  };
}

export default function LearningStageLanding({
  stages,
  onStageSelect,
  onCreatePlan,
}) {
  const frameRef = useRef(0);
  const cardRefs = useRef([]);
  const suppliedStages = Array.isArray(stages);
  const [remoteState, setRemoteState] = useState({ stages: [], loading: !suppliedStages, error: '', message: '' });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (suppliedStages) return undefined;
    let cancelled = false;
    loadPlannedLearningPath()
      .then((payload) => {
        if (cancelled) return;
        setRemoteState({
          stages: plannedStagesFromPath(payload),
          loading: false,
          error: '',
          message: String(payload?.message || ''),
        });
      })
      .catch((error) => {
        if (cancelled) return;
        setRemoteState({ stages: [], loading: false, error: error.message || '长期规划阶段加载失败', message: '' });
      });
    return () => { cancelled = true; };
  }, [reloadKey, suppliedStages]);

  const sourceStages = suppliedStages ? stages : remoteState.stages;
  const presentedStages = useMemo(
    () => sourceStages.map(withPresentation),
    [sourceStages],
  );
  const layout = useMemo(() => getStageLayout(presentedStages.length), [presentedStages.length]);

  useEffect(() => () => cancelAnimationFrame(frameRef.current), []);

  const resetMagnetism = () => {
    cancelAnimationFrame(frameRef.current);
    cardRefs.current.forEach((card) => {
      if (!card) return;
      card.style.setProperty('--magnetic-lift', '0px');
      card.style.setProperty('--magnetic-scale', '1');
    });
  };

  const updateMagnetism = (event) => {
    if (typeof window.matchMedia === 'function'
      && !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    const { clientX, clientY } = event;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = requestAnimationFrame(() => {
      cardRefs.current.forEach((card) => {
        if (!card) return;
        const rect = card.getBoundingClientRect();
        const distance = Math.hypot(clientX - (rect.left + rect.width / 2), clientY - (rect.top + rect.height / 2));
        const strength = Math.max(0, 1 - distance / 360);
        card.style.setProperty('--magnetic-lift', `${(strength * 14).toFixed(2)}px`);
        card.style.setProperty('--magnetic-scale', (1 + strength * 0.052).toFixed(3));
      });
    });
  };

  const selectStage = (stage, index, event) => {
    const sourceRect = snapshotRect(event.currentTarget.getBoundingClientRect());
    resetMagnetism();
    onStageSelect?.({ stage, index, sourceRect });
  };

  return (
    <main className="learning-stage" aria-labelledby="learning-stage-title">
      <span className="learning-stage__orb learning-stage__orb--large" aria-hidden="true" />
      <span className="learning-stage__orb learning-stage__orb--small" aria-hidden="true" />

      <header className="learning-stage__header">
        <p className="learning-stage__eyebrow">LEARNING WORKSHOP · 学习工坊</p>
        <h1 id="learning-stage-title">我的长期学习阶段</h1>
        <p>依据已保存的长期规划，按阶段进入教材与知识点</p>
      </header>

      {!suppliedStages && remoteState.loading && (
        <section className="learning-stage__empty" role="status" aria-live="polite">
          <strong>正在读取长期规划…</strong>
          <p>阶段数据将以当前用户已保存的长期规划为准。</p>
        </section>
      )}

      {!suppliedStages && !remoteState.loading && presentedStages.length === 0 && (
        <section className="learning-stage__empty" data-state={remoteState.error ? 'error' : 'empty'}>
          <strong>{remoteState.error ? '长期规划暂时无法读取' : '尚未生成长期规划阶段'}</strong>
          <p>{remoteState.error || remoteState.message || '请先通过智能助教制定长期规划，系统随后会生成阶段、教材和知识点路径。'}</p>
          {remoteState.error ? (
            <button type="button" onClick={() => {
              setRemoteState((current) => ({ ...current, loading: true, error: '' }));
              setReloadKey((value) => value + 1);
            }}>重新加载</button>
          ) : (
            <button type="button" onClick={onCreatePlan}>去制定长期规划</button>
          )}
        </section>
      )}

      {presentedStages.length > 0 && <>
      <ol
        className="learning-stage__markers"
        aria-label="学习阶段索引"
        style={{ '--stage-count': presentedStages.length }}
      >
        {presentedStages.map((stage) => (
          <li key={stage.id} style={{ '--marker-color': stage.colors[0] }}>
            <span aria-hidden="true" />
            <small>{stage.level}</small>
          </li>
        ))}
      </ol>

      <section className="learning-stage__staircase" aria-label="学习阶段">
        <div
          className="learning-stage__grid"
          data-testid="learning-stage-grid"
          data-stage-count={presentedStages.length}
          style={{ '--stage-count': presentedStages.length }}
          onPointerMove={updateMagnetism}
          onPointerLeave={resetMagnetism}
        >
          {presentedStages.map((stage, index) => (
            <button
              key={stage.id}
              ref={(node) => { cardRefs.current[index] = node; }}
              type="button"
              className="learning-stage-card"
              aria-label={`进入${stage.title}阶段`}
              data-resource-density={layout[index].progress < 0.5 ? 'compact' : 'full'}
              style={{
                '--stage-progress': layout[index].progress,
                '--stage-start': stage.colors[0],
                '--stage-end': stage.colors[1],
                '--illustration-position': stage.illustrationPosition || '65% 68%',
              }}
              onClick={(event) => selectStage(stage, index, event)}
            >
              {stage.illustration && (
                <img
                  className="learning-stage-card__illustration"
                  src={stage.illustration}
                  alt=""
                  aria-hidden="true"
                  decoding="async"
                  draggable="false"
                />
              )}
              <span className="learning-stage-card__number" aria-hidden="true">{index + 1}</span>
              <span className="learning-stage-card__title">{stage.title}</span>
              <span className="learning-stage-card__duration">{stage.duration}</span>
              <span className="learning-stage-card__tasks">
                {stage.tasks?.map((task) => (
                  <span key={task}><b aria-hidden="true">✓</b>{task}</span>
                ))}
              </span>
              {stage.resources?.length > 0 && (
                <span className="learning-stage-card__resources">
                  <small>推荐资源</small>
                  <span>{stage.resources.map((resource) => <em key={resource}>{resource}</em>)}</span>
                </span>
              )}
            </button>
          ))}
        </div>
      </section>

      <footer className="learning-stage__legend" aria-label="进阶图例">
        <span><i style={{ background: STAGE_PALETTE[0][0] }} />入门基础</span><b>→</b>
        <span><i style={{ background: STAGE_PALETTE[2][0] }} />提高进阶</span><b>→</b>
        <span><i style={{ background: STAGE_PALETTE[4][0] }} />专精深造</span><b>→</b>
        <span><i style={{ background: STAGE_PALETTE[5][0] }} />融会贯通</span>
      </footer>
      </>}
    </main>
  );
}
