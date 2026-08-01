import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeft, CheckCircle2, CircleDot, Clock3, Crosshair, MapPinned,
    RotateCcw, ShieldCheck,
} from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../../utils/api';
import {
    BUILTIN_ACUPUNCTURE_CASES,
    EMPTY_ACUPUNCTURE_CASE,
    getAcupunctureCaseDisplayTitle,
    normalizeAcupunctureCase,
} from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';
import AcupunctureModelCanvas from './AcupunctureModelCanvas';
import './acupuncturePractice.css';

const STEPS = ['配合意愿', '3D模型', '施针', '正确答案', '开始评分'];

// 进针类型：相对皮肤表面的进针角度（直刺≈90°、斜刺≈45°、平刺≈15°）
const INSERTION_OPTIONS = [
    ['direct', '直刺', 90],
    ['oblique', '斜刺', 45],
    ['transverse', '平刺', 15],
];
// 相对皮肤表面法线的倾斜角（surfaceAngle = 90 - tilt）
const INSERTION_TILT_BY_TYPE = { direct: 0, oblique: 45, transverse: 75 };

export default function AcupuncturePractice({ caseData = EMPTY_ACUPUNCTURE_CASE, onBack }) {
    const [cases, setCases] = useState(() => BUILTIN_ACUPUNCTURE_CASES.map(normalizeAcupunctureCase));
    const [selectedCaseId, setSelectedCaseId] = useState(caseData.caseId || BUILTIN_ACUPUNCTURE_CASES[0]?.caseId || '');
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
    const [submittedResult, setSubmittedResult] = useState(null);
    const needleSequence = useRef(0);

    useEffect(() => {
        let mounted = true;
        fetchWithAuth('/api/v1/simulated-patient/acupuncture-cases')
            .then((response) => readJsonResponse(response, {}))
            .then((payload) => {
                if (!mounted) return;
                const remoteCases = Array.isArray(payload?.data?.cases) ? payload.data.cases : [];
                const loaded = (remoteCases.length ? remoteCases : BUILTIN_ACUPUNCTURE_CASES)
                    .map(normalizeAcupunctureCase);
                setCases(loaded);
                if (loaded[0]) {
                    setSelectedCaseId((current) => current || loaded[0].caseId);
                }
            })
            .catch(() => {
                if (mounted) {
                    const fallback = BUILTIN_ACUPUNCTURE_CASES.map(normalizeAcupunctureCase);
                    setCases(fallback);
                    setSelectedCaseId((current) => current || fallback[0]?.caseId || '');
                }
            })
            .finally(() => { if (mounted) setCasesLoading(false); });
        return () => { mounted = false; };
    }, []);

    const activeCase = cases.find((item) => item.caseId === selectedCaseId) || caseData;
    const scoringCase = useMemo(() => ({
        ...activeCase,
        standardPoints: activeCase.standardPoints.map((point) => ({
            ...point,
            modelPosition: point.modelPosition || standardPositions[point.modelNodeName],
        })),
    }), [activeCase, standardPositions]);
    const localResult = useMemo(() => scoreAcupunctureAttempt(scoringCase, needles), [scoringCase, needles]);
    const result = submittedResult || localResult;
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
        setSubmittedResult(null);
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
        setSubmittedResult(null);
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

    const confirmFinish = () => {
        setStep(5);
    };

    const generateFeedback = async () => {
        let nextResult = localResult;
        if (activeCase.caseId) {
            try {
                const response = await fetchWithAuth('/api/v1/simulated-patient/acupuncture-score', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        case_id: activeCase.caseId,
                        needles,
                    }),
                });
                const payload = await readJsonResponse(response, {});
                if (payload?.success && payload?.data) {
                    nextResult = {
                        ...localResult,
                        ...payload.data,
                        feedback: payload.data.feedback || localResult.feedback,
                    };
                }
            } catch {
                // The local score remains available when the review endpoint is unavailable.
            }
        }
        setSubmittedResult(nextResult);
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

        if (step >= 2 && step <= 4) {
            const modelMode = step === 2 ? 'observe' : step === 3 ? 'needling' : 'review';
            const heading = step === 2 ? '观察 3D 人体模型' : step === 3 ? '开始下针' : '展示正确答案';
            const description = step === 2
                ? '旋转、缩放模型观察人体；可自主显示穴位和名称作为提示，正确答案不会提前高亮。'
                : step === 3
                    ? '按住并松开人体表面完成施针定位；拖拽旋转模型不会落针，提交前可撤销上一针并重新选择位置。'
                    : '下面显示本病例的标准穴位。当前答案已经锁定，确认后进入评分。';
            const StepIcon = step === 2 ? MapPinned : step === 3 ? Crosshair : CheckCircle2;

            return (
            <section className="acupuncture-card acupuncture-card--wide acupuncture-card--fullscreen">
                <span className="acupuncture-card__eyebrow"><StepIcon size={16} /> 第{step === 2 ? '二' : step === 3 ? '三' : '四'}步</span>
                <h2>{heading}</h2>
                <p>{description}</p>
                <div className="acupuncture-needling-layout">
                    <AcupunctureModelCanvas
                        mode={modelMode}
                        expanded
                        fullScreen
                        standardNodeNames={standardNodeNames}
                        onStandardPointsReady={setStandardPositions}
                        needles={needles}
                        onSurfacePick={placeModelNeedle}
                        showMarkers={showModelMarkers}
                        onToggleMarkers={() => setShowModelMarkers((value) => !value)}
                    />
                    {step === 3 && <div className="acupuncture-controls">
                        <div className="acupuncture-insertion-choice" role="group" aria-label="选择进针类型">
                            <span>进针类型</span>
                            <div className="acupuncture-choice-row">
                                {INSERTION_OPTIONS.map(([value, label, angle]) => (
                                    <button key={value} type="button" className={insertionType === value ? 'is-selected' : ''} aria-label={label} onClick={() => { setInsertionType(value); setTiltAngle(INSERTION_TILT_BY_TYPE[value]); }}>{label} {angle}°</button>
                                ))}
                            </div>
                        </div>
                        <label><span>进针角度 <strong>{90 - tiltAngle}°</strong></span><input type="range" min="5" max="90" step="1" value={90 - tiltAngle} onChange={(event) => { const value = Number(event.target.value); setTiltAngle(90 - value); }} /></label>
                        <label><span>进针深度 <strong>{depth} 寸</strong></span><input type="range" min="0" max="2.5" step="0.1" value={depth} onChange={(event) => { const value = Number(event.target.value); setDepth(value); }} /></label>
                        <label><span>留针时间 <strong>{retentionMinutes} 分钟</strong></span><input type="range" min="1" max="60" step="1" value={retentionMinutes} onChange={(event) => { const value = Number(event.target.value); setRetentionMinutes(value); }} /></label>
                        <div className="acupuncture-needle-summary">已记录 {needles.length} 个落针点{surfacePick ? ' · 最近一针已定位' : ''}</div>
                        <button className="acupuncture-secondary" disabled={!needles.length} onClick={() => setNeedles((current) => current.slice(0, -1))}>撤销上一针</button>
                    </div>}
                </div>
                <button className="acupuncture-secondary acupuncture-card--fullscreen-exit" onClick={onBack}><ArrowLeft size={16} /> 退出训练</button>
                {step === 2 && <button className="acupuncture-primary" onClick={() => setStep(3)}>开始下针</button>}
                {step === 3 && <button className="acupuncture-primary" disabled={!needles.length} onClick={completeNeedling}>完成施针</button>}
                {step === 4 && <>
                <div className="acupuncture-summary-list"><span>落针数量<strong>{needles.length} 针</strong></span><span>案例留针<strong>{caseRetentionMinutes ?? '未配置'} {caseRetentionRange?.unit || ''}</strong></span></div>
                <div className="acupuncture-case-parameters acupuncture-my-answer">
                    <strong>我的答案（共 {needles.length} 针）</strong>
                    {needles.map((needle, index) => (
                        <div className="acupuncture-my-answer-entry" key={needle.id || index}>
                            <b>第 {index + 1} 针</b>
                            <span>进针类型：{needle.insertionType === 'oblique' ? '斜刺' : needle.insertionType === 'transverse' ? '平刺' : '直刺'}</span>
                            <span>进针角度：{Number.isFinite(Number(needle.tiltAngle)) ? `${90 - Number(needle.tiltAngle)}°` : '未记录'}</span>
                            <span>进针深度：{needle.depthValue ?? '未记录'} 寸</span>
                            <span>留针时间：{needle.retentionMinutes ?? '未记录'} 分钟</span>
                        </div>
                    ))}
                </div>
                <div className="acupuncture-case-parameters acupuncture-correct-answer">
                    <strong>正确答案 · {primaryStandardPoint?.name || '标准穴位'} {primaryStandardPoint?.code ? `（${primaryStandardPoint.code}）` : ''}</strong>
                    <span>定位：{primaryStandardPoint?.locationDescription || '未配置'}</span>
                    <span>进针类型：{primaryStandardPoint?.insertionType === 'oblique' ? '斜刺' : primaryStandardPoint?.insertionType === 'transverse' ? '平刺' : '直刺'}</span>
                    <span>进针深度：{caseDepthRange ? `${caseDepthRange.min}–${caseDepthRange.max} ${depthUnit}` : '未配置'}</span>
                    <span>进针角度：{primaryStandardPoint?.needleAngle || '未配置'}</span>
                    <span>留针时间：{caseRetentionRange ? `${caseRetentionRange.min}–${caseRetentionRange.max} ${caseRetentionRange.unit}` : '未配置'}</span>
                </div>
                {activeCase.standardPoints.length
                    ? <div className="acupuncture-standard-list">{activeCase.standardPoints.map((point) => <span key={point.id || point.name}>{point.name}（{point.code}）<small>{point.locationDescription}</small>{point.procedureType === 'pricking_cupping' && <small>操作提示：{point.needleAngle || '刺络拔罐'}</small>}</span>)}</div>
                    : <div className="acupuncture-empty-data">标准穴位数据为空，已保留接入位置。</div>}
                <button className="acupuncture-primary" onClick={confirmFinish}>确认并进入评分</button>
                </>}
            </section>
            );
        }

        if (step === 5) return (
            <section className="acupuncture-card acupuncture-score-card">
                <span className="acupuncture-card__eyebrow"><Crosshair size={16} /> 第五步</span>
                <h2>开始评分</h2>
                {!scoreGenerated && <>
                    <div className="acupuncture-criteria"><article><MapPinned size={20} /><span>位置准确度</span><strong>{result.position === null ? '待数据' : `${result.position}%`}</strong></article><article><ShieldCheck size={20} /><span>进针类型</span><strong>{result.insertion === null ? '待数据' : `${result.insertion}%`}</strong></article><article><CircleDot size={20} /><span>深浅程度</span><strong>{result.depth === null ? '待数据' : `${result.depth}%`}</strong></article><article><Clock3 size={20} /><span>留针时间</span><strong>{result.retention === null ? '待数据' : `${result.retention}%`}</strong></article></div>
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
            <div className="acupuncture-case-picker"><label htmlFor="acupuncture-case">选择训练病例</label><select id="acupuncture-case" value={selectedCaseId} onChange={chooseCase} disabled={!cases.length}><option value="">{casesLoading ? '正在加载病例…' : '请选择病例'}</option>{cases.map((item) => <option key={item.caseId} value={item.caseId}>{item.caseId} · {getAcupunctureCaseDisplayTitle(item.title)}</option>)}</select></div>
            <nav className="acupuncture-steps" aria-label="针灸训练流程">{STEPS.map((label, index) => <span key={label} className={step === index + 1 ? 'is-current' : step > index + 1 ? 'is-complete' : ''}><b>{index + 1}</b>{label}</span>)}</nav>
            <main>{renderStep()}</main>
        </div>
    );
}
