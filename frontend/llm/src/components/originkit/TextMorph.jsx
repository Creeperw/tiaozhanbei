import React, { useId } from 'react';
import { useReducedMotion } from 'framer-motion';

export default function TextMorph({ active, idleText, activeText }) {
  const shouldReduceMotion = useReducedMotion();
  const filterId = `text-morph-${useId().replace(/:/g, '')}`;
  const longestText = activeText.length > idleText.length ? activeText : idleText;

  return (
    <span
      className="text-morph"
      data-active={active}
      data-reduced-motion={shouldReduceMotion}
    >
      <span className="text-morph__accessible">{active ? activeText : idleText}</span>
      <svg className="text-morph__filter" aria-hidden="true">
        <defs>
          <filter id={filterId} x="-40%" y="-120%" width="180%" height="340%">
            <feColorMatrix
              in="SourceGraphic"
              type="matrix"
              values="1 0 0 0 0
                      0 1 0 0 0
                      0 0 1 0 0
                      0 0 0 25 -9"
              result="goo"
            />
            <feComposite in="SourceGraphic" in2="goo" operator="atop" />
          </filter>
        </defs>
      </svg>
      <span
        className="text-morph__stage"
        style={active ? { filter: `url(#${filterId})` } : undefined}
        aria-hidden="true"
      >
        <span className="text-morph__anchor">{longestText}</span>
        <span className="text-morph__word text-morph__word--idle">{idleText}</span>
        <span className="text-morph__word text-morph__word--active">{activeText}</span>
      </span>
    </span>
  );
}
