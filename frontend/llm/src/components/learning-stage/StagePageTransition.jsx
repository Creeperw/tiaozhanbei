import React, { useEffect } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import './LearningStageLanding.css';
import { STAGE_FLIP_DURATION_MS } from './learningStageModel';

const MotionDiv = motion.div;

function targetGeometry(sourceRect) {
  const viewportWidth = typeof window === 'undefined' ? 1440 : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? 900 : window.innerHeight;
  const width = Math.min(Math.max(sourceRect.width, 320), viewportWidth - 32);
  const height = Math.min(Math.max(sourceRect.height, 400), viewportHeight - 32);
  return {
    x: Math.max(16, (viewportWidth - width) / 2),
    y: Math.max(16, (viewportHeight - height) / 2),
    width,
    height,
  };
}

export default function StagePageTransition({ selection, onMidpoint, onComplete }) {
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (!selection) return undefined;
    const duration = reducedMotion ? 80 : STAGE_FLIP_DURATION_MS;
    const midpointTimer = window.setTimeout(() => onMidpoint?.(selection), duration / 2);
    const completeTimer = window.setTimeout(() => onComplete?.(), duration);
    return () => {
      window.clearTimeout(midpointTimer);
      window.clearTimeout(completeTimer);
    };
  }, [selection, reducedMotion, onMidpoint, onComplete]);

  if (!selection) return null;

  const { stage, index, sourceRect } = selection;
  const target = targetGeometry(sourceRect);
  const durationSeconds = reducedMotion ? 0.08 : STAGE_FLIP_DURATION_MS / 1000;
  const ease = [0.22, 1, 0.36, 1];
  const faceStyle = {
    '--stage-start': stage.colors?.[0] || '#3F8F68',
    '--stage-end': stage.colors?.[1] || '#2E7150',
    '--transition-illustration-position': stage.illustrationPosition || '65% 68%',
  };
  const statusTitle = stage.title.endsWith('阶段') ? stage.title : `${stage.title}阶段`;

  return (
    <div
      className="stage-page-transition"
      role="status"
      aria-label={`正在进入${statusTitle}`}
      aria-live="polite"
    >
      <MotionDiv
        className="stage-page-transition__backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: reducedMotion ? 0.92 : [0, 0.72, 0.92] }}
        transition={{ duration: durationSeconds, ease }}
      />
      <MotionDiv
        className="stage-page-transition__shell"
        initial={{ x: sourceRect.left, y: sourceRect.top, width: sourceRect.width, height: sourceRect.height }}
        animate={reducedMotion ? target : { ...target, scale: [1, 1.025, 1] }}
        transition={{ duration: durationSeconds, ease }}
      >
        <MotionDiv
          className="stage-page-transition__flipper"
          initial={{ rotateY: 0 }}
          animate={{ rotateY: reducedMotion ? 0 : 180 }}
          transition={{ duration: durationSeconds, ease }}
        >
          <div
            className="stage-page-transition__face stage-page-transition__face--front"
            style={faceStyle}
          >
            {stage.illustration && (
              <img
                className="stage-page-transition__illustration"
                src={stage.illustration}
                alt=""
                aria-hidden="true"
              />
            )}
            <div className="stage-page-transition__copy">
              <span className="stage-page-transition__badge">{index + 1}</span>
              <strong className="stage-page-transition__title">{stage.title}</strong>
              <small className="stage-page-transition__detail">{stage.duration}</small>
            </div>
          </div>
          <div
            className="stage-page-transition__face stage-page-transition__face--back"
            style={faceStyle}
          >
            {stage.illustration && (
              <img
                className="stage-page-transition__illustration"
                src={stage.illustration}
                alt=""
                aria-hidden="true"
              />
            )}
            <div className="stage-page-transition__copy">
              <span className="stage-page-transition__badge stage-page-transition__badge--route">经典路线</span>
              <strong className="stage-page-transition__title">{stage.title}</strong>
              <small className="stage-page-transition__detail">正在展开学习路径</small>
            </div>
          </div>
        </MotionDiv>
      </MotionDiv>
    </div>
  );
}
