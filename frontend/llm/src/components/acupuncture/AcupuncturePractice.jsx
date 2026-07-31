import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeft, CheckCircle2, CircleDot, Clock3, Crosshair, MapPinned,
    RotateCcw, ShieldCheck,
} from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../../utils/api';
import {
    EMPTY_ACUPUNCTURE_CASE,
    getAcupunctureCaseDisplayTitle,
    normalizeAcupunctureCase,
} from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';
import AcupunctureModelCanvas from './AcupunctureModelCanvas';
import './acupuncturePractice.css';

const STEPS = ['配合意愿', '3D模型', '施针', '正确答案', '开始评分'];

export default function AcupuncturePractice({ caseData = EMPTY_ACUPUNCTURE_CASE, onBack }) {
    const [cases, setCases] = useState([]);
    const [selectedCaseId, setSelectedCaseId] = useState(caseData.caseId || '');
    const [casesLoading, setCasesLoading] = useState(true);
    const [step, setStep] = useState(1);
    const [consent, setConsent] = useState(null);
    const [insertionType, setInsertionType] = useState('direct');
    const [tiltAngle, setTiltAngle] = useState(0);
    const [directionAngle, setDirectionAngle] = useState(0);
    const [depth, setDepth] = useState(0.5);
    const [retentionMinutes, setRetentionMinutes] = useState(20);
    const [needles, setNeedles] = useState([]);
    const [showModelMarkers, setShowModelMarkers] = useState(false);
    const [surfacePick, setSurfacePick] = useState(null);
    const [standardPositions, setStandardPositions] = useState({});
    const [scoreGenerated, setScoreGenerated] = useState(false);
    const needleSequence = useRef(0);

    useEffect(() => {
        let mounted = true;
        fetchWithAuth('/api/v1/simulated-patient/acupuncture-cases')
            .then((response) => readJsonResponse(response, {}))
            .then((payload) => {
                if (!mounted) return;
                const loaded = Array.isArray(payload?.data?.cases)
                    ? payload.data.cases.map(normalizeAcupunctureCase)
                    : [];
                setCases(loaded);
                if (loaded[0]) {
                    setSelectedCaseId((current) => current || loaded[0].caseId);
                }
            })
            .catch(() => { })
            .finally(() => { if (mounted) setCasesLoading(false); });
        return () => { mounted = false; };
    }, []);

    const activeCase = cases.find((item) => item.caseId === selectedCaseId) || caseData;
    const scoringCase = useMemo(() => ({
        ...activeCase,
        standardPoints: activeCase.standardPoints.map((point) => ({
            ...point,
            modelPosition: standardPositions[point.modelNodeName],
        })),
    }), [activeCase, standardPositions]);
    const result = useMemo(() => scoreAcupunctureAttempt(scoringCase, needles), [scoringCase, needles]);
    const standardNodeNames = useMemo(
        () => activeCase.standardPoints.map((point) => point.modelNodeName).filter(Boolean),
        [activeCase],
    );
    const primaryStandardPoint = activeCase.standardPoints[0];
    const caseDepthRange = primaryStandardPoint?.depthRange;
    const caseRetentionRange = primaryStandardPoint?.retentionRange;
    const depthUnit = caseDepthRange?.unit || '寸';
    const caseRetentionMinutes = caseRetentionRange
        && caseRetentionRange.unit === '分钟'
        && Number.isFinite(caseRetentionRange.min)
        && Number.isFinite(caseRetentionRange.max)
        ? Math.round((caseRetentionRange.min + caseRetentionRange.max) / 2)
        : null;
    const resetCaseState = () => {
        setConsent(null);
        setNeedles([]);
        setSurfacePick(null);
        setStandardPositions({});
        setShowModelMarkers(false);
        setScoreGenerated(false);
        setTiltAngle(0);
        setInsertionType('direct');
        setDirectionAngle(0);
        setDepth(0.5);
        setRetentionMinutes(20);
        setStep(1);
    };

    const chooseCase = (event) => {
        const nextCaseId = event.target.value;
        setSelectedCaseId(nextCaseId);
        resetCaseState();
    };
    const restart = () => {
        setStep(1);
        setConsent(null);
        setNeedles([]);
        setSurfacePick(null);
        setStandardPositions({});
        setShowModelMarkers(false);
        setScoreGenerated(false);
        setTiltAngle(0);
        setInsertionType('direct');
        setDirectionAngle(0);
        setDepth(0.5);
        setRetentionMinutes(20);
    };

    const completeNeedling = () => {
        setStep(4);
    };

    const placeModelNeedle = (pick) => {
        setSurfacePick(pick);
        needleSequence.current += 1;
        setNeedles((current) => [...current, {
            id: `needle-${needleSequence.current}`,
            regionId: 'multi-region',
            point: pick.point,
            normal: pick.normal,
            insertionType,
            tiltAngle,
            directionAngle,
            depthValue: depth,
            depthUnit: '寸',
            depthMm: null,
            retentionMinutes,
        }]);
    };

    const updateLatestNeedle = (updates) => {
        setNeedles((current) => current.map((needle, index) => (
            index === current.length - 1 ? { ...needle, ...updates } : needle
        )));
    };

    const confirmFinish = () => {
        setShowModelMarkers(true);
        setStep(5);
    };

    const generateFeedback = async () => {
        if (activeCase.caseId && Object.keys(standardPositions).length) {
            try {
                await fetchWithAuth('/api/v1/simulated-patient/acupuncture-score', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        case_id: activeCase.caseId,
                        needles,
                        standard_positions: standardPositions,
                    }),
                });
            } catch {
                // The local score remains available when the review endpoint is unavailable.
            }
        }
        setScoreGenerated(true);
    };

    const renderStep = () => {
        if (step === 1) return (
            <section className="acupuncture-card">
                <span className="acupuncture-card__eyebrow"><ShieldCheck size={16} /> 第一步</span>
                <h2>确认患者配合意愿</h2>
                {activeCase.complaint && <div className="acupuncture-case-complaint">主诉：{activeCase.complaint}</div>}
                {activeCase.historySummary && <p>{activeCase.historySummary}</p>}
                <p>{activeCase.patientConsentPrompt}</p>
                <div className="acupuncture-choice-row">
                    {activeCase.consentOptions?.length ? activeCase.consentOptions.map((option) => (
                        <button key={option.text} className={consent === option.isCorrect ? 'is-selected' : ''} onClick={() => setConsent(Boolean(option.isCorrect))}>{option.text}</button>
                    )) : <>
                        <button className={consent === true ? 'is-selected' : ''} onClick={() => setConsent(true)}>愿意配合</button>
                        <button className={consent === false ? 'is-selected is-declined' : ''} onClick={() => setConsent(false)}>暂不愿意</button>
                    </>}
                </div>
                {consent === false && <div className="acupuncture-notice">患者暂不配合针灸，应停止操作并进行沟通或更换病例。</div>}
                <button className="acupuncture-primary" disabled={consent !== true} onClick={() => setStep(2)}>进入 3D 模型</button>
            </section>
        );

        if (step === 2) return (
            <section className="acupuncture-card acupuncture-card--wide acupuncture-card--fullscreen">
                <span className="acupuncture-card__eyebrow"><MapPinned size={16} /> 第二步</span>
                <h2>观察 3D 人体模型</h2>
                <p>旋转、缩放模型观察人体；点击左上角按钮显示或隐藏穴位。准备好后直接进入施针。</p>
                <AcupunctureModelCanvas expanded fullScreen standardNodeNames={standardNodeNames} onStandardPointsReady={setStandardPositions} needles={needles} showMarkers={showModelMarkers} revealStandardPoints={false} onToggleMarkers={() => setShowModelMarkers((value) => !value)} />
                <button className="acupuncture-secondary acupuncture-card--fullscreen-exit" onClick={onBack}><ArrowLeft size={16} /> 退出训练</button>
                <button className="acupuncture-primary" onClick={() => setStep(3)}>开始下针</button>
            </section>
        );

        if (step === 3) return (
            <section className="acupuncture-card acupuncture-card--wide acupuncture-card--fullscreen">
                <span className="acupuncture-card__eyebrow"><Crosshair size={16} /> 第三步</span>
                <h2>开始下针</h2>
                <p>点击人体表面完成施针定位；提交前可撤销上一针并重新选择位置。</p>
                <div className="acupuncture-needling-layout">
                    <AcupunctureModelCanvas expanded fullScreen standardNodeNames={standardNodeNames} onStandardPointsReady={setStandardPositions} needles={needles} interactive onSurfacePick={placeModelNeedle} showMarkers={showModelMarkers} revealStandardPoints={false} onToggleMarkers={() => setShowModelMarkers((value) => !value)} />
                    <button className="acupuncture-secondary acupuncture-card--fullscreen-exit" onClick={onBack}><ArrowLeft size={16} /> 退出训练</button>
                    <div className="acupuncture-controls">
                        <div className="acupuncture-insertion-choice" role="group" aria-label="选择进针类型">
                            <span>进针类型</span>
                            <div className="acupuncture-choice-row">
                                {[['direct', '直刺'], ['oblique', '斜刺'], ['transverse', '平刺']].map(([value, label]) => (
                                    <button key={value} type="button" className={insertionType === value ? 'is-selected' : ''} onClick={() => { setInsertionType(value); updateLatestNeedle({ insertionType: value }); }}>{label}</button>
                                ))}
                            </div>
                        </div>
                        <label><span>进针深度 <strong>{depth} 寸</strong></span><input type="range" min="0" max="2.5" step="0.1" value={depth} onChange={(event) => { const value = Number(event.target.value); setDepth(value); updateLatestNeedle({ depthValue: value }); }} /></label>
                        <label><span>留针时间 <strong>{retentionMinutes} 分钟</strong></span><input type="range" min="1" max="60" step="1" value={retentionMinutes} onChange={(event) => { const value = Number(event.target.value); setRetentionMinutes(value); updateLatestNeedle({ retentionMinutes: value }); }} /></label>
                        <div className="acupuncture-needle-summary">已记录 {needles.length} 个落针点{surfacePick ? ' · 最近一针已定位' : ''}</div>
                        <button className="acupuncture-secondary" disabled={!needles.length} onClick={() => setNeedles((current) => current.slice(0, -1))}>撤销上一针</button>
                    </div>
                </div>
                <button className="acupuncture-primary" disabled={!needles.length} onClick={completeNeedling}>完成施针</button>
            </section>
        );

        if (step === 4) return (
            <section className="acupuncture-card acupuncture-card--fullscreen">
                <span className="acupuncture-card__eyebrow"><CheckCircle2 size={16} /> 第四步</span>
                <h2>展示正确答案</h2>
                <div className="acupuncture-summary-list"><span>落针数量<strong>{needles.length} 针</strong></span><span>案例留针<strong>{caseRetentionMinutes ?? '未配置'} {caseRetentionRange?.unit || ''}</strong></span></div>
                <p>下面显示本病例的标准穴位。确认后进入评分，本次施针操作将不再修改。</p>
                <AcupunctureModelCanvas expanded fullScreen standardNodeNames={standardNodeNames} onStandardPointsReady={setStandardPositions} needles={needles} showMarkers={showModelMarkers} revealStandardPoints onToggleMarkers={() => setShowModelMarkers((value) => !value)} />
                <button className="acupuncture-secondary acupuncture-card--fullscreen-exit" onClick={onBack}><ArrowLeft size={16} /> 退出训练</button>
                <div className="acupuncture-case-parameters">
                    <strong>本案例施针标准</strong>
                    <span>角度：{primaryStandardPoint?.needleAngle || '未配置'}</span>
                    <span>深度：{caseDepthRange ? `${caseDepthRange.min}–${caseDepthRange.max} ${depthUnit}` : '未配置'}</span>
                    <span>留针：{caseRetentionRange ? `${caseRetentionRange.min}–${caseRetentionRange.max} ${caseRetentionRange.unit}` : '未配置'}</span>
                </div>
                {activeCase.standardPoints.length
                    ? <div className="acupuncture-standard-list">{activeCase.standardPoints.map((point) => <span key={point.id || point.name}>{point.name}（{point.code}）<small>{point.locationDescription}</small></span>)}</div>
                    : <div className="acupuncture-empty-data">标准穴位数据为空，已保留接入位置。</div>}
                <button className="acupuncture-primary" onClick={confirmFinish}>开始评分</button>
            </section>
        );

        if (step === 5) return (
            <section className="acupuncture-card acupuncture-score-card">
                <span className="acupuncture-card__eyebrow"><Crosshair size={16} /> 第五步</span>
                <h2>开始评分</h2>
                {!scoreGenerated && <>
                    <div className="acupuncture-criteria"><article><MapPinned size={20} /><span>位置准确度</span><strong>{result.position === null ? '待数据' : `${result.position}%`}</strong></article><article><CircleDot size={20} /><span>深浅程度</span><strong>{result.depth === null ? '待数据' : `${result.depth}%`}</strong></article><article><Clock3 size={20} /><span>留针时间</span><strong>{result.retention === null ? '待数据' : `${result.retention}%`}</strong></article></div>
                    {!result.available && <div className="acupuncture-notice">当前病例坐标或单位标准未完整配置，只保存操作结果，不生成伪判定。</div>}
                    <button className="acupuncture-primary" onClick={generateFeedback}>生成评分反馈</button>
                </>}
                {scoreGenerated && <>
                    <div className="acupuncture-score">{result.total === null ? '--' : result.total}<small>分</small></div>
                    <p>{result.feedback}</p>
                    <div className="acupuncture-score-actions"><button className="acupuncture-secondary" onClick={onBack}><ArrowLeft size={16} /> 返回模式选择</button><button className="acupuncture-primary" onClick={restart}><RotateCcw size={16} /> 再练一次</button></div>
                </>}
            </section>
        );

    };

    return (
        <div className="acupuncture-practice">
            <header className="acupuncture-header">
                <button onClick={onBack}><ArrowLeft size={17} /> 返回模式选择</button>
                <div><span>模拟病患 · 针灸专练</span><h1>{getAcupunctureCaseDisplayTitle(activeCase.title || '针灸操作训练')}</h1></div>
            </header>
            <div className="acupuncture-case-picker"><label htmlFor="acupuncture-case">选择训练病例</label><select id="acupuncture-case" value={selectedCaseId} onChange={chooseCase} disabled={casesLoading || !cases.length}><option value="">{casesLoading ? '正在加载病例…' : '暂无病例'}</option>{cases.map((item) => <option key={item.caseId} value={item.caseId}>{item.caseId} · {getAcupunctureCaseDisplayTitle(item.title)}</option>)}</select></div>
            <nav className="acupuncture-steps" aria-label="针灸训练流程">{STEPS.map((label, index) => <span key={label} className={step === index + 1 ? 'is-current' : step > index + 1 ? 'is-complete' : ''}><b>{index + 1}</b>{label}</span>)}</nav>
            <main>{renderStep()}</main>
        </div>
    );
}
