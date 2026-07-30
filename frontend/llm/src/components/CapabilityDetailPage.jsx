import React from 'react';
import { ArrowRight, Check, ChevronRight } from 'lucide-react';
import { getPlatformCapability } from '../platformCapabilities';
import './CapabilityDetailPage.css';

export default function CapabilityDetailPage({ capabilityKey, onNavigate }) {
  const capability = getPlatformCapability(capabilityKey);
  const Icon = capability.icon;

  return (
    <main className={`capability-detail capability-detail--${capability.tone}`}>
      <section className="capability-detail__hero" aria-labelledby="capability-detail-title">
        <div className="capability-detail__hero-copy">
          <p className="capability-detail__eyebrow">
            <span>核心能力 {capability.index}</span>
            {capability.eyebrow}
          </p>
          <div className="capability-detail__title-row">
            <span className="capability-detail__title-icon">
              <Icon aria-hidden="true" size={32} strokeWidth={1.8} />
            </span>
            <h1 id="capability-detail-title">{capability.title}</h1>
          </div>
          <p className="capability-detail__lead">{capability.description}</p>
          <button
            type="button"
            className="capability-detail__primary-action"
            onClick={() => onNavigate?.(capability.targetIntent)}
          >
            {capability.actionLabel}
            <ArrowRight aria-hidden="true" size={18} />
          </button>
        </div>

        <div className="capability-detail__preview" aria-label={`${capability.title}功能预览`}>
          <div className="capability-detail__preview-head">
            <span>FUNCTION MAP</span>
            <strong>功能协同视图</strong>
          </div>
          <div className="capability-detail__preview-map">
            <span className="capability-detail__preview-line" aria-hidden="true" />
            {capability.preview.map((item, index) => (
              <div className="capability-detail__preview-node" key={item.label}>
                <span>{String(index + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{item.label}</strong>
                  <small>{item.detail}</small>
                </div>
                <Check aria-hidden="true" size={15} />
              </div>
            ))}
          </div>
          <div className="capability-detail__preview-status">
            <span aria-hidden="true" />
            能力模块已就绪
          </div>
        </div>
      </section>

      <section className="capability-detail__features" aria-labelledby="capability-features-title">
        <header className="capability-detail__section-head">
          <div>
            <span>WHAT IT DOES</span>
            <h2 id="capability-features-title">核心功能</h2>
          </div>
          <p>围绕真实学习场景，提供从理解、执行到反馈的完整支持。</p>
        </header>
        <div className="capability-detail__feature-grid">
          {capability.features.map((feature, index) => {
            const FeatureIcon = feature.icon;
            return (
              <article key={feature.title} className="capability-detail__feature-card">
                <div className="capability-detail__feature-meta">
                  <span className="capability-detail__feature-icon">
                    <FeatureIcon aria-hidden="true" size={22} />
                  </span>
                  <small>{String(index + 1).padStart(2, '0')}</small>
                </div>
                <h3>{feature.title}</h3>
                <p>{feature.description}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="capability-detail__workflow" aria-labelledby="capability-workflow-title">
        <div className="capability-detail__workflow-copy">
          <span>HOW IT WORKS</span>
          <h2 id="capability-workflow-title">功能如何运转</h2>
          <p>三个步骤串联成清晰的使用路径，让能力真正服务于学习结果。</p>
        </div>
        <ol className="capability-detail__workflow-steps">
          {capability.flow.map((step, index) => (
            <li key={step}>
              <span>{index + 1}</span>
              <strong>{step}</strong>
              {index < capability.flow.length - 1 && <ChevronRight aria-hidden="true" size={18} />}
            </li>
          ))}
        </ol>
      </section>

      <section className="capability-detail__cta" aria-label={`体验${capability.title}`}>
        <div>
          <span>READY TO START</span>
          <h2>现在开始体验{capability.title}</h2>
          <p>进入实际功能模块，在你的学习目标和真实数据中体验智能学习支持。</p>
        </div>
        <button type="button" onClick={() => onNavigate?.(capability.targetIntent)}>
          {capability.actionLabel}
          <ArrowRight aria-hidden="true" size={18} />
        </button>
      </section>
    </main>
  );
}
