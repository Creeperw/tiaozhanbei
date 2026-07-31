import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import AcupuncturePractice from './AcupuncturePractice';
import { EMPTY_ACUPUNCTURE_CASE, getAcupunctureCaseDisplayTitle, normalizeAcupunctureCase, resolveRegionImage } from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';

vi.mock('./AcupunctureModelCanvas', () => ({
    default: ({ interactive, onSurfacePick }) => (
        <div data-testid="acupuncture-model-canvas">
            {interactive && <button type="button" onClick={() => onSurfacePick({ point: [0, 0, 0], normal: [0, 1, 0] })}>记录一个落针点</button>}
        </div>
    ),
}));

describe('AcupuncturePractice', () => {
    it('follows consent directly into the 3D model and needling steps', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        expect(screen.getByText('确认患者配合意愿')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        expect(screen.getByText('观察 3D 人体模型')).toBeInTheDocument();
        expect(screen.queryByText('选择针灸部位')).not.toBeInTheDocument();
        expect(screen.queryByText('对应部位示意')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        expect(screen.getByText('点击人体表面完成施针定位；提交前可撤销上一针并重新选择位置。')).toBeInTheDocument();
        expect(screen.queryByText('本案例施针标准')).not.toBeInTheDocument();
        expect(screen.getByRole('group', { name: '选择进针类型' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '直刺' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '斜刺' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '平刺' })).toBeInTheDocument();
        expect(screen.getByText('进针深度')).toBeInTheDocument();
        expect(screen.getByText('留针时间')).toBeInTheDocument();
    });

    it.each([
        { advanceTo: [] },
        { advanceTo: ['开始下针'] },
        { advanceTo: ['开始下针', '记录一个落针点', '完成施针'] },
    ])('keeps an exit action available during fullscreen 3D training', ({ advanceTo }) => {
        const onBack = vi.fn();
        render(<AcupuncturePractice onBack={onBack} />);

        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        advanceTo.forEach((label) => fireEvent.click(screen.getByRole('button', { name: label })));
        fireEvent.click(screen.getByRole('button', { name: '退出训练' }));

        expect(onBack).toHaveBeenCalledOnce();
    });

    it('derives insertion type from each standard needle-angle instruction', () => {
        const normalized = normalizeAcupunctureCase({
            standardAcupoints: [
                { name: '太溪', needleAngle: '直刺0.5-0.8寸' },
                { name: '劳宫', needleAngle: '向掌心斜刺0.5-0.8寸' },
                { name: '阿是穴', needleAngle: '仅刺络拔罐，不作针刺' },
            ],
        });

        expect(normalized.standardPoints.map((point) => point.insertionType)).toEqual([
            'direct', 'oblique', undefined,
        ]);
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
                needleAngle: '直刺0.5-0.8寸',
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
                needleAngle: '直刺0.5-0.8寸',
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance: { value: 15, unit: 'px' },
        });

        expect(normalized.applicableRegions).toEqual(['feet']);
        expect(normalized.standardPoints[0]).toMatchObject({ name: '太溪', code: 'KI3', regionId: 'feet' });
        expect(normalized.scoring.positionTolerancePercent).toBeNull();
        expect(normalized.standardPoints[0].needleAngle).toBe('直刺0.5-0.8寸');
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

    it('matches insertion type against the needle position standard', () => {
        const result = scoreAcupunctureAttempt({
            ...EMPTY_ACUPUNCTURE_CASE,
            standardPoints: [
                { id: 'p1', name: '直刺穴位', regionId: 'back', x: 20, y: 20, insertionType: 'direct' },
                { id: 'p2', name: '斜刺穴位', regionId: 'back', x: 80, y: 80, insertionType: 'oblique' },
            ],
            scoring: { positionTolerancePercent: 5 },
        }, [
            { x: 20, y: 20, insertionType: 'direct' },
            { x: 80, y: 80, insertionType: 'direct' },
        ]);

        expect(result.insertion).toBe(50);
    });

    it('scores configured position, depth, retention and insertion standards', () => {
        const result = scoreAcupunctureAttempt({
            ...EMPTY_ACUPUNCTURE_CASE,
            standardPoints: [{ id: 'p1', name: '测试穴位', regionId: 'back', x: 50, y: 50, insertionType: 'direct' }],
            scoring: {
                positionTolerancePercent: 5,
                depthRangeMm: [15, 25],
                retentionRangeMinutes: [15, 30],
            },
        }, [{ regionId: 'back', x: 52, y: 51, insertionType: 'oblique', depthMm: 20, retentionMinutes: 20 }]);

        expect(result).toMatchObject({ available: true, position: 100, depth: 100, retention: 100, insertion: 0 });
        expect(result.total).toBe(75);
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