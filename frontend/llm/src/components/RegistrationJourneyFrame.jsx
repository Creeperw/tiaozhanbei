import React from 'react';
import { LogOut } from 'lucide-react';
import './RegistrationJourney.css';

export const REGISTRATION_TOTAL_STEPS = 8;

export default function RegistrationJourneyFrame({
  step,
  totalSteps = REGISTRATION_TOTAL_STEPS,
  eyebrow,
  title,
  description,
  mascotMessage,
  onExit,
  exitLabel = '返回展示页',
  children,
}) {
  const progress = Math.round((step / totalSteps) * 100);

  return (
    <div className="registration-journey">
      <header className="registration-journey__header">
        <div className="registration-journey__brand">
          <span><strong>时珍智训</strong><small>建立你的专属学习路径</small></span>
        </div>
        {onExit && (
          <button type="button" className="registration-journey__exit" onClick={onExit}>
            <LogOut size={17} aria-hidden="true" />
            {exitLabel}
          </button>
        )}
      </header>

      <main className="registration-journey__main">
        <aside className="registration-journey__guide" aria-label="李时珍学习向导">
          <div className="registration-journey__speech" role="status">{mascotMessage}</div>
          <img
            src="/assistant-character/lizhizhen-center-cutout.png"
            alt="Q版李时珍学习向导"
            className="registration-journey__mascot"
            draggable="false"
          />
        </aside>

        <section className="registration-journey__panel" aria-labelledby="registration-step-title">
          <div className="registration-journey__progress-row">
            <div
              className="registration-journey__progress"
              role="progressbar"
              aria-label="注册与学情调查进度"
              aria-valuemin="1"
              aria-valuemax={totalSteps}
              aria-valuenow={step}
            >
              <span style={{ width: `${progress}%` }} />
            </div>
            <strong>{step} / {totalSteps}</strong>
          </div>
          <div className="registration-journey__heading">
            <span>{eyebrow}</span>
            <h1 id="registration-step-title">{title}</h1>
            <p>{description}</p>
          </div>
          {children}
        </section>
      </main>
    </div>
  );
}
