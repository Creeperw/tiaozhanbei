import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowRight,
  BrainCircuit,
} from 'lucide-react';
import { PLATFORM_CAPABILITIES } from '../platformCapabilities';
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

export default function HomePage({ onNavigate }) {
  const [videoFailed, setVideoFailed] = useState(false);
  const videoRef = useRef(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (reducedMotion) videoRef.current?.pause();
  }, [reducedMotion]);

  const navigate = (intent) => onNavigate?.(intent);
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
            <p className="platform-home__eyebrow">AI 驱动的中医药智能学习平台</p>
            <h1 id="platform-home-title">
              多智能体协同，<br />让中医药学习<span>更高效</span>
            </h1>
            <p className="platform-home__description">
              融合多智能体协同与中医知识图谱，个性化规划学习路径，
              实时伴学答疑，精准提升学习效果，助力资格考试通关。
            </p>
          </div>

          <div className="platform-home__actions">
            <button
              type="button"
              className="platform-home__primary-action"
              onClick={() => navigate({ page: 'learning-path', params: {} })}
            >
              开始学习路径
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
    </main>
  );
}
