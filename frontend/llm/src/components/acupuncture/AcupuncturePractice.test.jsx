import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import AcupuncturePractice from './AcupuncturePractice';
import { EMPTY_ACUPUNCTURE_CASE, getAcupunctureCaseDisplayTitle, normalizeAcupunctureCase, resolveRegionImage } from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';

vi.mock('./AcupunctureModelCanvas', () => ({
    default: ({ mode, onSurfacePick }) => (
        <div data-testid="acupuncture-model-canvas" data-mode={mode}>
            <button type="button">显示穴位</button>
            <button type="button">显示穴位名称</button>
            {mode === 'review' && <span data-testid="standard-answer-highlighted">正确穴位橙色高亮</span>}
            {mode === 'needling' && <button type="button" onClick={() => onSurfacePick({ point: [0, 0, 0], normal: [0, 1, 0] })}>记录一个落针点</button>}
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
        const persistentModel = screen.getByTestId('acupuncture-model-canvas');
        expect(persistentModel).toHaveAttribute('data-mode', 'observe');
        expect(screen.getByRole('button', { name: '显示穴位' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '显示穴位名称' })).toBeInTheDocument();
        expect(screen.queryByTestId('standard-answer-highlighted')).not.toBeInTheDocument();
        expect(screen.queryByText('选择针灸部位')).not.toBeInTheDocument();
        expect(screen.queryByText('对应部位示意')).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        expect(screen.getByText('按住并松开人体表面完成施针定位；拖拽旋转模型不会落针，提交前可撤销上一针并重新选择位置。')).toBeInTheDocument();
        expect(screen.getByTestId('acupuncture-model-canvas')).toBe(persistentModel);
        expect(screen.getByTestId('acupuncture-model-canvas')).toHaveAttribute('data-mode', 'needling');
        expect(screen.queryByTestId('standard-answer-highlighted')).not.toBeInTheDocument();
        expect(screen.queryByText('本案例施针标准')).not.toBeInTheDocument();
        expect(screen.getByRole('group', { name: '选择进针类型' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '直刺' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '斜刺' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '平刺' })).toBeInTheDocument();
        expect(screen.getByText('进针深度')).toBeInTheDocument();
        expect(screen.getByText('留针时间')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));
        expect(screen.getByTestId('acupuncture-model-canvas')).toBe(persistentModel);
        expect(screen.getByTestId('acupuncture-model-canvas')).toHaveAttribute('data-mode', 'review');
        expect(screen.getByTestId('standard-answer-highlighted')).toBeInTheDocument();
    });

    it('shows my needling answer above the correct answer card', () => {
        const testCase = normalizeAcupunctureCase({
            caseId: 'acup-test',
            title: '测试病例 — 太溪穴',
            standardAcupoints: [{
                name: '太溪',
                code: 'KI3',
                modelNodeName: 'taixi',
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                needleAngle: '直刺0.5-0.8寸',
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
        });
        render(<AcupuncturePractice caseData={testCase} onBack={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));

        expect(screen.getByText(/我的答案/)).toBeInTheDocument();
        expect(screen.getAllByText(/进针类型：直刺/)).toHaveLength(2);
        expect(screen.getByText(/进针深度：0.5 寸/)).toBeInTheDocument();
        expect(screen.getByText(/留针时间：20 分钟/)).toBeInTheDocument();
        const myAnswer = screen.getByText(/我的答案/);
        const correctAnswer = screen.getByText(/正确答案 · 太溪/);
        expect(myAnswer).toBeInTheDocument();
        expect(screen.queryByText(/我的答案 · 太溪/)).not.toBeInTheDocument();
        expect(myAnswer.compareDocumentPosition(correctAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('keeps every needle in my answer records', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));

        expect(screen.getByText('我的答案（共 2 针）')).toBeInTheDocument();
        expect(screen.getByText('第 1 针')).toBeInTheDocument();
        expect(screen.getByText('第 2 针')).toBeInTheDocument();
    });

    it('offers fallback cases when the cases endpoint is empty', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        const picker = screen.getByRole('combobox', { name: '选择训练病例' });
        expect(picker).not.toBeDisabled();
        expect(picker.querySelectorAll('option')).toHaveLength(11);
    });

    it('shows my needling answer above the correct answer card', () => {
        const testCase = normalizeAcupunctureCase({
            caseId: 'acup-test',
            title: '测试病例 — 太溪穴',
            standardAcupoints: [{
                name: '太溪',
                code: 'KI3',
                modelNodeName: 'taixi',
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                needleAngle: '直刺0.5-0.8寸',
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
        });
        render(<AcupuncturePractice caseData={testCase} onBack={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));

        expect(screen.getByText(/我的答案/)).toBeInTheDocument();
        expect(screen.getAllByText(/进针类型：直刺/)).toHaveLength(2);
        expect(screen.getByText(/进针深度：0.5 寸/)).toBeInTheDocument();
        expect(screen.getByText(/留针时间：20 分钟/)).toBeInTheDocument();
        const myAnswer = screen.getByText(/我的答案/);
        const correctAnswer = screen.getByText(/正确答案 · 太溪/);
        expect(myAnswer).toBeInTheDocument();
        expect(screen.queryByText(/我的答案 · 太溪/)).not.toBeInTheDocument();
        expect(myAnswer.compareDocumentPosition(correctAnswer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('keeps every needle in my answer records', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));

        expect(screen.getByText('我的答案（共 2 针）')).toBeInTheDocument();
        expect(screen.getByText('第 1 针')).toBeInTheDocument();
        expect(screen.getByText('第 2 针')).toBeInTheDocument();
    });

    it('offers fallback cases when the cases endpoint is empty', () => {
        render(<AcupuncturePractice onBack={vi.fn()} />);

        const picker = screen.getByRole('combobox', { name: '选择训练病例' });
        expect(picker).not.toBeDisabled();
        expect(picker.querySelectorAll('option')).toHaveLength(11);
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

    it('scores each matched user needle against the standard instead of fabricating full credit', () => {
        const normalized = normalizeAcupunctureCase({
            standardAcupoints: [{
                name: '太溪',
                region: 'feet',
                coordinates: { x: 50, y: 50, unit: 'px' },
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                needleAngle: '直刺0.5-0.8寸',
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance: { value: 15, unit: 'px' },
        });

        expect(scoreAcupunctureAttempt(normalized, [{
            x: 50, y: 50, insertionType: 'oblique', depthValue: 1.2, retentionMinutes: 40,
        }])).toMatchObject({
            available: true, position: 100, depth: 0, retention: 0, insertion: 0,
        });
        expect(scoreAcupunctureAttempt(normalized, [{
            x: 80, y: 80, insertionType: 'direct', depthValue: 0.6, retentionMinutes: 20,
        }])).toMatchObject({
            available: true, position: 0, depth: 100, retention: 100, insertion: 0,
        });
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

    it('uses archived surface positions and gives partial credit near a standard point', () => {
        const normalized = normalizeAcupunctureCase({
            standardAcupoints: [{
                name: '太溪',
                modelPosition: [0, 0, 0],
                procedureType: 'needling',
                insertionType: 'direct',
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance3d: { excellent: 0.012, pass: 0.03, outer: 0.06 },
        });

        expect(normalized.standardPoints[0].modelPosition).toEqual([0, 0, 0]);
        expect(scoreAcupunctureAttempt(normalized, [{
            point: [0.021, 0, 0], insertionType: 'direct', depthValue: 0.6, retentionMinutes: 20,
        }])).toMatchObject({ position: 80, insertion: 100, depth: 100, retention: 100, total: 95 });
    });

    it('matches multi-point attempts against each point-specific standard', () => {
        const result = scoreAcupunctureAttempt({
            ...EMPTY_ACUPUNCTURE_CASE,
            standardPoints: [
                {
                    name: '液门', modelPosition: [0, 0, 0], procedureType: 'needling', insertionType: 'direct',
                    depthRange: { min: 0.3, max: 0.5 }, retentionRange: { min: 25, max: 35 },
                },
                {
                    name: '外关', modelPosition: [1, 0, 0], procedureType: 'needling', insertionType: 'direct',
                    depthRange: { min: 0.5, max: 1.0 }, retentionRange: { min: 25, max: 35 },
                },
            ],
            scoring: { positionToleranceExcellent: 0.012, positionTolerancePass: 0.03, positionToleranceOuter: 0.06 },
        }, [
            { point: [1, 0, 0], insertionType: 'direct', depthValue: 0.8, retentionMinutes: 30 },
            { point: [0, 0, 0], insertionType: 'direct', depthValue: 0.4, retentionMinutes: 30 },
        ]);

        expect(result).toMatchObject({ total: 100, position: 100, insertion: 100, depth: 100, retention: 100 });
    });

    it('keeps pricking-cupping in the answer but excludes it from ordinary needling scores', () => {
        const result = scoreAcupunctureAttempt({
            ...EMPTY_ACUPUNCTURE_CASE,
            standardPoints: [
                {
                    name: '承山', modelPosition: [0, 0, 0], procedureType: 'needling', insertionType: 'direct',
                    depthRange: { min: 1, max: 2 }, retentionRange: { min: 25, max: 35 },
                },
                {
                    name: '委中', modelPosition: [1, 0, 0], procedureType: 'pricking_cupping', insertionType: null,
                    depthRange: { min: 0, max: 0 }, retentionRange: { min: 8, max: 12 },
                },
            ],
            scoring: { positionToleranceExcellent: 0.012, positionTolerancePass: 0.03, positionToleranceOuter: 0.06 },
        }, [{ point: [0, 0, 0], insertionType: 'direct', depthValue: 1.5, retentionMinutes: 30 }]);

        expect(result).toMatchObject({ total: 100, position: 100, insertion: 100, depth: 100, retention: 100 });
    });

    it('shows the backend score response as the final result', async () => {
        const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) => ({
            status: 200,
            text: async () => JSON.stringify(String(url).endsWith('/acupuncture-score')
                ? {
                    success: true,
                    data: {
                        available: true,
                        total: 73,
                        position: 70,
                        insertion: 80,
                        depth: 60,
                        retention: 80,
                        feedback: '后端权威评分反馈',
                    },
                }
                : { success: true, data: { cases: [] } }),
        }));
        const testCase = normalizeAcupunctureCase({
            caseId: 'acup-server-score',
            title: '评分接口病例',
            standardAcupoints: [{
                name: '太溪', code: 'KI3', modelNodeName: 'taixi', modelPosition: [0, 0, 0],
                procedureType: 'needling', insertionType: 'direct',
                needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
                needleAngle: '直刺0.5-0.8寸', retentionTime: { min: 15, max: 25, unit: '分钟' },
            }],
            positionTolerance3d: { excellent: 0.012, pass: 0.03, outer: 0.06 },
        });

        render(<AcupuncturePractice caseData={testCase} onBack={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: '愿意配合' }));
        fireEvent.click(screen.getByRole('button', { name: '进入 3D 模型' }));
        fireEvent.click(screen.getByRole('button', { name: '开始下针' }));
        fireEvent.click(screen.getByRole('button', { name: '记录一个落针点' }));
        fireEvent.click(screen.getByRole('button', { name: '完成施针' }));
        fireEvent.click(screen.getByRole('button', { name: '确认并进入评分' }));
        fireEvent.click(screen.getByRole('button', { name: '生成评分反馈' }));

        expect(await screen.findByText('后端权威评分反馈')).toBeInTheDocument();
        expect(screen.getByText('73')).toBeInTheDocument();
        expect(fetchMock).toHaveBeenCalledWith(
            '/api/v1/simulated-patient/acupuncture-score',
            expect.objectContaining({ method: 'POST' }),
        );
        fetchMock.mockRestore();
    });
});
