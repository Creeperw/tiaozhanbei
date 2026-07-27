import { describe, expect, it } from 'vitest';
import { textbookPlanningLabel } from './textbookPlanning';

const thirteenFiveBooks = [
  '\u523a\u6cd5\u7078\u6cd5\u5b66',
  '\u4e2d\u897f\u533b\u7ed3\u5408\u5185\u79d1\u5b66',
  '\u4eba\u4f53\u89e3\u5256\u5b66',
  '\u4e2d\u897f\u533b\u7ed3\u5408\u5916\u79d1\u5b66',
  '\u836f\u7528\u690d\u7269\u5b66',
  '\u9488\u7078\u533b\u7c4d\u9009\u8bfb',
  '\u4e2d\u56fd\u533b\u5b66\u53f2',
  '\u4e2d\u897f\u533b\u7ed3\u5408\u8033\u9f3b\u54bd\u5589\u79d1\u5b66',
  '\u4e2d\u836f\u5206\u6790',
  '\u4e2d\u836f\u70ae\u5236\u5b66',
  '\u4e2d\u836f\u836f\u5242\u5b66',
  '\u4e2d\u533b\u6c14\u529f\u5b66',
];

describe('textbookPlanningLabel', () => {
  it('labels the configured thirteen-five textbooks', () => {
    for (const name of thirteenFiveBooks) {
      expect(textbookPlanningLabel(name)).toBe('\u5341\u4e09\u4e94\u89c4\u5212\u6559\u6750');
    }
    expect(textbookPlanningLabel('\u300a\u4eba\u4f53\u89e3\u5256\u5b66\u300b')).toBe('\u5341\u4e09\u4e94\u89c4\u5212\u6559\u6750');
  });

  it('does not override labels for other textbooks', () => {
    expect(textbookPlanningLabel('\u4e2d\u533b\u5b66\u57fa\u7840')).toBeNull();
  });
});
