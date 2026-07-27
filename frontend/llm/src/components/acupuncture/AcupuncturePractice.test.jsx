import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import AcupuncturePractice from './AcupuncturePractice';
import { EMPTY_ACUPUNCTURE_CASE, getAcupunctureCaseDisplayTitle, normalizeAcupunctureCase, resolveRegionImage } from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';

describe('AcupuncturePractice', () => {
    it('follows the consent, region and body-image steps', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        expect(screen.getByText('确认患者配合意愿')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入部位选择' }));
        expect(screen.getByRole('button', { name: '足部' })).not.toHaveClass('is-selected');
        fireEvent.click(screen.getByRole('button', { name: /背部/ }));
        fireEvent.click(screen.getByRole('button', { name: '查看对应部位' }));

        expect(screen.getByText('对应部位示意')).toBeInTheDocument();
        expect(screen.getByRole('img', { name: 'back部位示意图' })).toHaveAttribute('src', '/acupuncture/back.png');
    });

    it('does not fabricate a score while standard data is empty', () => {
        expect(scoreAcupunctureAttempt(EMPTY_ACUPUNCTURE_CASE, [{
            regionId: 'back', x: 50, y: 50, depthMm: 20, retentionMinutes: 20,
        }])).toMatchObject({ available: false, total: null });
    });

    it('keeps depth and retention available when coordinates are placeholders', () => {
        const normalized = normalizeAcupunctureCase({
            standardAcupoints: [{
                name: '太溪',
                region: 'feet',
                coordinates: { x: 0, y: 0, unit: 'px' },
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance: { value: 15, unit: 'px' },
        });

        expect(normalized.scoring.depthRange).toEqual([0.5, 0.8]);
        expect(normalized.scoring.depthUnit).toBe('寸');
        expect(scoreAcupunctureAttempt(normalized, [{ depthValue: 0.6, retentionMinutes: 20 }]))
            .toMatchObject({ available: true, position: null, depth: 100, retention: 100 });
    });

    it('uses the selected region image instead of the case default image', () => {
        expect(resolveRegionImage('back', { feet: '/acupuncture/feet.png' })).toBe('/acupuncture/back.png');
        expect(resolveRegionImage('feet', { feet: '/custom/feet.png' })).toBe('/custom/feet.png');
    });

    it('hides acupuncture point strategy from the case picker title', () => {
        expect(getAcupunctureCaseDisplayTitle('膝关节痛（足太阴经证）— 隐白穴通经止痛'))
            .toBe('膝关节痛（足太阴经证）');
        expect(getAcupunctureCaseDisplayTitle('双小腿发凉（下肢寒痹）— 承山穴+委中刺络拔罐'))
            .toBe('双小腿发凉（下肢寒痹）');
    });

    it('maps source cases while keeping placeholder coordinates unscored', () => {
        const normalized = normalizeAcupunctureCase({
            caseId: 'acup-test',
            title: '测试病例',
            applicableRegions: ['feet'],
            standardAcupoints: [{
                name: '太溪',
                code: 'KI3',
                region: 'feet',
                coordinates: { x: 0, y: 0, unit: 'px' },
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance: { value: 15, unit: 'px' },
        });

        expect(normalized.applicableRegions).toEqual(['feet']);
        expect(normalized.standardPoints[0]).toMatchObject({ name: '太溪', code: 'KI3', regionId: 'feet' });
        expect(normalized.scoring.positionTolerancePercent).toBeNull();
    });

    it('keeps the consent question focused on reassurance instead of point selection', () => {
        const normalized = normalizeAcupunctureCase({
            cooperationWillingness: {
                patientLine: '我有点害怕。',
                candidateOptions: [
                    { text: '原始正确选项包含太溪穴和留针时间', isCorrect: true },
                    { text: '原始错误选项', isCorrect: false },
                ],
            },
        });

        expect(normalized.consentOptions[0].text).toContain('理解您的担心');
        expect(normalized.consentOptions.map((option) => option.text).join('')).not.toContain('太溪穴');
        expect(normalized.consentOptions[0].isCorrect).toBe(true);
    });

    it('scores configured position, depth and retention standards', () => {
        const result = scoreAcupunctureAttempt({
            ...EMPTY_ACUPUNCTURE_CASE,
            standardPoints: [{ id: 'p1', name: '测试穴位', regionId: 'back', x: 50, y: 50 }],
            scoring: {
                positionTolerancePercent: 5,
                depthRangeMm: [15, 25],
                retentionRangeMinutes: [15, 30],
            },
        }, [{ regionId: 'back', x: 52, y: 51, depthMm: 20, retentionMinutes: 20 }]);

        expect(result).toMatchObject({ available: true, total: 100, position: 100, depth: 100, retention: 100 });
    });
});