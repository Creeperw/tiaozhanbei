import { describe, expect, it } from 'vitest';
import {
  focusMinutesFromStatistics,
  formatLearningDuration,
  normalizeBookName,
  selectNextKnowledgePoint,
} from './learningPlanDashboard';

describe('learning plan dashboard selectors', () => {
  it('reads the lifetime focus duration and formats it consistently', () => {
    expect(focusMinutesFromStatistics({ lifetime: { focus_minutes: 5160 } })).toBe(5160);
    expect(formatLearningDuration(5160)).toBe('86 小时');
    expect(formatLearningDuration(45)).toBe('45 分钟');
    expect(formatLearningDuration(null)).toBe('待统计');
  });

  it('normalizes book names and selects the first unfinished knowledge point', () => {
    expect(normalizeBookName('《中医学基础》')).toBe('中医学基础');
    expect(selectNextKnowledgePoint({
      items: [
        { status: 'completed', kp_name: '阴阳' },
        { status: 'in_progress', kp_name: '五行生克' },
      ],
      focus_knowledge_points: ['不应优先'],
    })).toBe('五行生克');
  });
});
