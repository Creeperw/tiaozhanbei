import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  BrainCircuit,
} from 'lucide-react';
import { PLATFORM_CAPABILITIES } from '../platformCapabilities';
import ScrollTextReveal from './originkit/ScrollTextReveal';
import QualificationTargetDialog from './QualificationTargetDialog';
import './PlatformHome.css';

function getReducedMotionPreference() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function useReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(getReducedMotionPreference);

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const updatePreference = (event) => setReducedMotion(event.matches);
    query.addEventListener?.('change', updatePreference);
    return () => query.removeEventListener?.('change', updatePreference);
  }, []);

  return reducedMotion;
}

export default function HomePage({ currentUser, onNavigate, onLoginRequested }) {
  const [videoFailed, setVideoFailed] = useState(false);
  const [targetDialogOpen, setTargetDialogOpen] = useState(false);
  const videoRef = useRef(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (reducedMotion) videoRef.current?.pause();
  }, [reducedMotion]);

  const navigate = (intent) => onNavigate?.(intent);
  const startLearning = () => {
    if (currentUser === null) {
      onLoginRequested?.();
      return;
    }
    setTargetDialogOpen(true);
  };
  const handleVideoCanPlay = () => {
    if (reducedMotion) {
      videoRef.current?.pause();
      return;
    }

    videoRef.current?.play()?.catch(() => setVideoFailed(true));
  };

  return (
    <main className="platform-home">
      <section className="platform-home__hero" aria-labelledby="platform-home-title">
        <div className="platform-home__copy">
          <div className="platform-home__headline">
            <p className="platform-home__eyebrow">面向中医药资格证书考试的智能备考平台</p>
            <ScrollTextReveal id="platform-home-title" className="platform-home__title-reveal" />
            <p className="platform-home__description">
              围绕考试大纲与个人学习进度，提供学情诊断、学习规划、专项练习和实时答疑，
              帮助你查漏补缺，稳步提升备考效率。
            </p>
          </div>

          <div className="platform-home__actions">
            <button
              type="button"
              className="platform-home__primary-action"
              onClick={startLearning}
            >
              开始学习
              <ArrowRight aria-hidden="true" size={19} />
            </button>
          </div>
        </div>

        <div className="platform-home__visual" aria-label="多智能体协同学习演示">
          <div className="platform-home__visual-glow" aria-hidden="true" />
          {videoFailed ? (
            <div
              className="platform-home__video-fallback"
              data-testid="platform-video-fallback"
              aria-hidden="true"
            >
              <span className="platform-home__fallback-orbit platform-home__fallback-orbit--outer" />
              <span className="platform-home__fallback-orbit platform-home__fallback-orbit--inner" />
              <BrainCircuit className="platform-home__fallback-mark" size={72} strokeWidth={1.25} />
            </div>
          ) : (
            <video
              ref={videoRef}
              className="platform-home__video"
              src="/platform-assets/home/platform-agents.mp4?v=20260727"
              autoPlay={!reducedMotion}
              muted
              loop={!reducedMotion}
              playsInline
              preload="metadata"
              aria-label="多智能体协同学习动画"
              onLoadedData={handleVideoCanPlay}
              onError={() => setVideoFailed(true)}
            />
          )}
        </div>
      </section>

      <section className="platform-home__capabilities" aria-label="平台核心能力">
        {PLATFORM_CAPABILITIES.map((capability) => {
          const Icon = capability.icon;
          return (
            <button
              key={capability.title}
              type="button"
              className={`platform-home__capability platform-home__capability--${capability.tone}`}
              aria-label={`${capability.title}：${capability.shortDescription}`}
              onClick={() => navigate({
                page: 'capability-detail',
                params: { capability: capability.key },
              })}
            >
              <span className="platform-home__capability-icon">
                <Icon aria-hidden="true" size={25} />
              </span>
              <span className="platform-home__capability-copy">
                <strong>{capability.title}</strong>
                <span>{capability.shortDescription}</span>
              </span>
              <ArrowRight
                className="platform-home__capability-arrow"
                aria-hidden="true"
                size={19}
              />
            </button>
          );
        })}
      </section>
      {targetDialogOpen && (
        <QualificationTargetDialog
          onCancel={() => setTargetDialogOpen(false)}
          onSaved={(selected) => {
            window.dispatchEvent(new CustomEvent('shizhen:learning-target-changed', {
              detail: selected,
            }));
            setTargetDialogOpen(false);
            navigate({ page: 'learning-path', params: {} });
          }}
        />
      )}
    </main>
  );
}
