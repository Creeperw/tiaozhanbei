import React, { useEffect, useRef, useState } from 'react';
import {
  Activity,
  ArrowRight,
  BookOpen,
  BrainCircuit,
  Route,
} from 'lucide-react';
import LearningTargetSelector from './LearningTargetSelector';
import './PlatformHome.css';

const CAPABILITIES = [
  {
    title: '多智能体协同',
    description: '规划、答疑、评估智能体协同工作，贯穿学习全流程。',
    icon: BrainCircuit,
    intent: { page: 'assistant', params: {} },
    tone: 'emerald',
  },
  {
    title: '个性化学习路径',
    description: '基于目标和学习基础，动态生成清晰、高效的进阶路径。',
    icon: Route,
    intent: { page: 'learning-path', params: {} },
    tone: 'blue',
  },
  {
    title: '知识图谱驱动',
    description: '连接中医知识脉络，让重点、关联与薄弱环节一目了然。',
    icon: BookOpen,
    intent: { page: 'knowledge', params: { view: 'atlas' } },
    tone: 'violet',
  },
  {
    title: '数据驱动成长',
    description: '追踪学习表现与能力变化，持续优化下一步学习策略。',
    icon: Activity,
    intent: { page: 'personalization', params: {} },
    tone: 'orange',
  },
];

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

  return (
    <main className="platform-home">
      <section className="platform-home__hero" aria-labelledby="platform-home-title">
        <div className="platform-home__copy">
          <LearningTargetSelector className="platform-home__target-selector" />

          <div className="platform-home__headline">
            <p className="platform-home__eyebrow">AI 驱动的中医药智能学习平台</p>
            <h1 id="platform-home-title">
              多智能体协同，让中医药学习<span>更高效</span>
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
            <button
              type="button"
              className="platform-home__secondary-action"
              onClick={() => navigate({
                page: 'assistant',
                params: { newConversation: true },
              })}
            >
              了解多智能体如何协同
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
              src="/design-images/home/platform-agents.mp4"
              autoPlay={!reducedMotion}
              muted
              loop={!reducedMotion}
              playsInline
              preload="metadata"
              aria-label="多智能体协同学习动画"
              onLoadedData={() => {
                if (reducedMotion) videoRef.current?.pause();
              }}
              onError={() => setVideoFailed(true)}
            />
          )}
        </div>
      </section>

      <section className="platform-home__capabilities" aria-label="平台核心能力">
        {CAPABILITIES.map((capability) => {
          const Icon = capability.icon;
          return (
            <button
              key={capability.title}
              type="button"
              className={`platform-home__capability platform-home__capability--${capability.tone}`}
              aria-label={`${capability.title}：${capability.description}`}
              onClick={() => navigate(capability.intent)}
            >
              <span className="platform-home__capability-icon">
                <Icon aria-hidden="true" size={25} />
              </span>
              <span className="platform-home__capability-copy">
                <strong>{capability.title}</strong>
                <span>{capability.description}</span>
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
