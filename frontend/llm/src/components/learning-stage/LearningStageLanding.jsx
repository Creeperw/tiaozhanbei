import React, { useEffect, useMemo, useRef } from 'react';
import './LearningStageLanding.css';
import {
  DEFAULT_LEARNING_STAGES,
  getStageLayout,
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
  stages = DEFAULT_LEARNING_STAGES,
  onStageSelect,
}) {
  const frameRef = useRef(0);
  const cardRefs = useRef([]);
  const presentedStages = useMemo(
    () => (Array.isArray(stages) && stages.length ? stages : DEFAULT_LEARNING_STAGES)
      .map(withPresentation),
    [stages],
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
        <h1 id="learning-stage-title">中医药学习·进阶之路</h1>
        <p>从入门到精通的完整学习路径规划</p>
      </header>

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
    </main>
  );
}
