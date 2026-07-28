import React from 'react';
import { motion as Motion, useReducedMotion } from 'framer-motion';

const titleLines = [
  '多智能体助力学习，',
  '让中医学习与考证更高效',
];

export default function ScrollTextReveal({ className = '', id }) {
  const shouldReduceMotion = useReducedMotion();
  const supportsViewportObserver = typeof IntersectionObserver !== 'undefined';
  const animationProps = supportsViewportObserver
    ? {
      initial: shouldReduceMotion ? false : 'hidden',
      whileInView: 'visible',
      viewport: { once: true, amount: 0.6 },
    }
    : { initial: false, animate: 'visible' };

  return (
    <Motion.h1
      id={id}
      className={className}
      aria-label="多智能体助力学习，让中医学习与考证更高效"
      {...animationProps}
    >
      {titleLines.map((line, lineIndex) => (
        <span className="scroll-text-reveal__line" key={lineIndex}>
          {[...line].map((character, characterIndex) => (
            <span className="scroll-text-reveal__character-wrap" key={`${character}-${characterIndex}`}>
              <Motion.span
                className={lineIndex === 0 && characterIndex < 4 ? 'scroll-text-reveal__accent' : undefined}
                variants={{
                  hidden: { opacity: 0, y: '108%', filter: 'blur(8px)' },
                  visible: {
                    opacity: 1,
                    y: '0%',
                    filter: 'blur(0px)',
                    transition: {
                      duration: 0.5,
                      delay: lineIndex * 0.4 + characterIndex * 0.075,
                      ease: [0.22, 1, 0.36, 1],
                    },
                  },
                }}
              >
                {character}
              </Motion.span>
            </span>
          ))}
        </span>
      ))}
    </Motion.h1>
  );
}
