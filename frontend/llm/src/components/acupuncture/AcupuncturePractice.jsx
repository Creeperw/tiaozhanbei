import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    ArrowLeft, CheckCircle2, CircleDot, Clock3, Crosshair, MapPinned,
    RotateCcw, ShieldCheck,
} from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../../utils/api';
import {
    ACUPUNCTURE_REGION_OPTIONS,
    EMPTY_ACUPUNCTURE_CASE,
    getAcupunctureCaseDisplayTitle,
    normalizeAcupunctureCase,
    resolveRegionImage,
} from './acupuncturePracticeData';
import { scoreAcupunctureAttempt } from './acupunctureScoring';
import './acupuncturePractice.css';

const STEPS = ['配合意愿', '选择部位', '部位示意', '开始下针', '施针结束', '标准穴位', '判断正误', '评分反馈'];
const clamp = (value, minimum, maximum) => Math.min(Math.max(value, minimum), maximum);

export default function AcupuncturePractice({ caseData = EMPTY_ACUPUNCTURE_CASE, onBack }) {
    const [cases, setCases] = useState([]);
    const [selectedCaseId, setSelectedCaseId] = useState(caseData.caseId || '');
    const [casesLoading, setCasesLoading] = useState(true);
    const [step, setStep] = useState(1);
    const [consent, setConsent] = useState(null);
    const [selectedRegions, setSelectedRegions] = useState([]);
    const [depth, setDepth] = useState(0.5);
    const [retentionMinutes, setRetentionMinutes] = useState(20);
    const [needles, setNeedles] = useState([]);
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
    const result = useMemo(() => scoreAcupunctureAttempt(activeCase, needles), [activeCase, needles]);
    const depthUnit = activeCase.standardPoints[0]?.depthRange?.unit || '寸';
    const activeRegion = selectedRegions[0] || '';
    const activeImage = resolveRegionImage(activeRegion, activeCase.regionImages);

    const resetCaseState = () => {
        setSelectedRegions([]);
        setConsent(null);
        setNeedles([]);
        setStep(1);
    };

    const chooseCase = (event) => {
        const nextCaseId = event.target.value;
        setSelectedCaseId(nextCaseId);
        resetCaseState();
    };
    const toggleRegion = (regionId) => setSelectedRegions((current) => current.includes(regionId)
        ? current.filter((item) => item !== regionId)
        : [...current, regionId]);

    const placeNeedle = (event, regionIdParam) => {
        const bounds = event.currentTarget.getBoundingClientRect();
        const x = clamp(((event.clientX - bounds.left) / (bounds.width || 1)) * 100, 0, 100);
        const y = clamp(((event.clientY - bounds.top) / (bounds.height || 1)) * 100, 0, 100);
        const regionForNeedle = regionIdParam || selectedRegions[0] || 'multi-region';
        needleSequence.current += 1;
        const nid = `needle-${needleSequence.current}`;
        const needle = {
            id: nid,
            regionId: regionForNeedle,
            x: Math.round(x * 10) / 10,
            y: Math.round(y * 10) / 10,
            depthValue: depth,
            depthUnit: activeCase.standardPoints[0]?.depthRange?.unit || '寸',
            depthMm: depth,
            retentionMinutes,
        };
        setNeedles((current) => [...current, needle]);
    };

    const restart = () => {
        setStep(1);
        setConsent(null);
        setNeedles([]);
        setDepth(0.5);
        setRetentionMinutes(20);
    };

    const completeNeedling = () => {
        setStep(5);
    };

    const confirmFinish = () => {
        setStep(6);
    };

    const generateFeedback = () => {
        setStep(8);
    };

    const renderBodyMapFor = (regionId, { interactive = false, showStandards = false } = {}) => {
        const imageForRegion = resolveRegionImage(regionId, activeCase.regionImages);
        return (
            <div
                key={regionId}
                className={`acupuncture-map${interactive ? ' is-interactive' : ''}`}
                onClick={interactive ? (e) => placeNeedle(e, regionId) : undefined}
                role={interactive ? 'button' : 'img'}
                tabIndex={interactive ? 0 : undefined}
                aria-label={interactive ? `人体部位示意图，点击放置针位-${regionId}` : '人体部位示意图占位'}
            >
                {imageForRegion ? <img src={imageForRegion} alt={`${regionId}部位示意图`} /> : (
                    <svg viewBox="0 0 240 360" aria-hidden="true">
                        <circle cx="120" cy="42" r="28" />
                        <path d="M85 82 Q120 66 155 82 L170 190 Q156 210 146 210 L160 326 L124 326 L120 226 L116 326 L80 326 L94 210 Q84 210 70 190 Z" />
                        <path d="M84 98 L38 202 M156 98 L202 202" />
                    </svg>
                )}
                <div className="acupuncture-map__regions">
                    <span>{ACUPUNCTURE_REGION_OPTIONS.find((item) => item.id === regionId)?.label || regionId}</span>
                </div>
                {needles.filter(n => n.regionId === regionId).map((needle, index) => (
                    <span key={needle.id} className="acupuncture-map__needle" style={{ left: `${needle.x}%`, top: `${needle.y}%` }}>{index + 1}</span>
                ))}
                {showStandards && activeCase.standardPoints.filter((point) => (
                    point.regionId === regionId && Number.isFinite(point.x) && Number.isFinite(point.y) && !(point.x === 0 && point.y === 0)
                )).map((point, index) => (
                    <span key={point.id || `${point.name}-${index}`} className="acupuncture-map__standard" style={{ left: `${point.x || 0}%`, top: `${point.y || 0}%` }} title={point.name} />
                ))}
                <small>{imageForRegion ? '点击图片记录落针位置' : '部位图片数据占位'}</small>
            </div>
        );
    };

    const renderBodyMaps = ({ interactive = false, showStandards = false } = {}) => {
        if (!selectedRegions.length) {
            // fallback to single activeRegion if nothing selected
            const region = activeRegion || Object.keys(activeCase.regionImages || {})[0] || 'multi-region';
            return renderBodyMapFor(region, { interactive, showStandards });
        }
        return (
            <div className="acupuncture-maps-grid">
                {selectedRegions.map((regionId) => renderBodyMapFor(regionId, { interactive, showStandards }))}
            </div>
        );
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
                <button className="acupuncture-primary" disabled={consent !== true} onClick={() => setStep(2)}>进入部位选择</button>
            </section>
        );

        if (step === 2) return (
            <section className="acupuncture-card">
                <span className="acupuncture-card__eyebrow"><MapPinned size={16} /> 第二步</span>
                <h2>选择针灸部位</h2>
                <p>请选择一个或多个需要施针的部位。</p>
                <div className="acupuncture-region-grid">
                    {ACUPUNCTURE_REGION_OPTIONS.map((region) => (
                        <button key={region.id} className={selectedRegions.includes(region.id) ? 'is-selected' : ''} onClick={() => toggleRegion(region.id)}><CircleDot size={17} />{region.label}</button>
                    ))}
                </div>
                <button className="acupuncture-primary" disabled={!selectedRegions.length} onClick={() => setStep(3)}>查看对应部位</button>
            </section>
        );

        if (step === 3) return (
            <section className="acupuncture-card acupuncture-card--wide">
                <span className="acupuncture-card__eyebrow"><MapPinned size={16} /> 第三步</span>
                <h2>对应部位示意</h2>
                <p>病例图片：{activeImage || '待接入'}。</p>
                {renderBodyMaps()}
                <button className="acupuncture-primary" onClick={() => setStep(4)}>开始下针</button>
            </section>
        );

        if (step === 4) return (
            <section className="acupuncture-card acupuncture-card--wide">
                <span className="acupuncture-card__eyebrow"><Crosshair size={16} /> 第四步</span>
                <h2>开始下针</h2>
                <p>点击示意图记录落针位置，并设置本次进针深度和留针时间。</p>
                <div className="acupuncture-needling-layout">
                    {renderBodyMaps({ interactive: true })}
                    <div className="acupuncture-controls">
                        <label><span>进针深度 <strong>{depth} {depthUnit}</strong></span><input type="range" min="0" max="2.5" step="0.1" value={depth} onChange={(event) => setDepth(Number(event.target.value))} /></label>
                        <label><span>留针时间 <strong>{retentionMinutes} 分钟</strong></span><input type="range" min="1" max="60" value={retentionMinutes} onChange={(event) => setRetentionMinutes(Number(event.target.value))} /></label>
                        <div className="acupuncture-needle-summary">已记录 {needles.length} 个落针点</div>
                        <button className="acupuncture-secondary" disabled={!needles.length} onClick={() => setNeedles((current) => current.slice(0, -1))}>撤销上一针</button>
                    </div>
                </div>
                <button className="acupuncture-primary" disabled={!needles.length} onClick={completeNeedling}>完成施针</button>
            </section>
        );

        if (step === 5) return (
            <section className="acupuncture-card">
                <span className="acupuncture-card__eyebrow"><CheckCircle2 size={16} /> 第五步</span>
                <h2>确认施针结束</h2>
                <div className="acupuncture-summary-list"><span>选择部位<strong>{selectedRegions.length} 个</strong></span><span>落针数量<strong>{needles.length} 针</strong></span><span>设置留针<strong>{retentionMinutes} 分钟</strong></span></div>
                <p>确认后进入标准穴位揭示环节，本次操作将不再修改。</p>
                <button className="acupuncture-primary" onClick={confirmFinish}>确认施针结束</button>
            </section>
        );

        if (step === 6) return (
            <section className="acupuncture-card acupuncture-card--wide">
                <span className="acupuncture-card__eyebrow"><MapPinned size={16} /> 第六步</span>
                <h2>显示应扎穴位</h2>
                {renderBodyMaps({ showStandards: true })}
                {activeCase.standardPoints.length
                    ? <div className="acupuncture-standard-list">{activeCase.standardPoints.map((point) => <span key={point.id || point.name}>{point.name}（{point.code}）<small>{point.locationDescription}</small></span>)}</div>
                    : <div className="acupuncture-empty-data">标准穴位数据为空，已保留接入位置。</div>}
                <button className="acupuncture-primary" onClick={() => setStep(7)}>进入操作判定</button>
            </section>
        );

        if (step === 7) return (
            <section className="acupuncture-card">
                <span className="acupuncture-card__eyebrow"><Crosshair size={16} /> 第七步</span>
                <h2>判断正误</h2>
                <div className="acupuncture-criteria"><article><MapPinned size={20} /><span>位置准确度</span><strong>{result.position === null ? '待数据' : `${result.position}%`}</strong></article><article><CircleDot size={20} /><span>深浅程度</span><strong>{result.depth === null ? '待数据' : `${result.depth}%`}</strong></article><article><Clock3 size={20} /><span>留针时间</span><strong>{result.retention === null ? '待数据' : `${result.retention}%`}</strong></article></div>
                {!result.available && <div className="acupuncture-notice">当前病例坐标或单位标准未完整配置，只保存操作结果，不生成伪判定。</div>}
                <button className="acupuncture-primary" onClick={generateFeedback}>生成评分反馈</button>
            </section>
        );

        return (
            <section className="acupuncture-card acupuncture-score-card">
                <span className="acupuncture-card__eyebrow"><CheckCircle2 size={16} /> 第八步</span>
                <h2>训练评分与反馈</h2>
                <div className="acupuncture-score">{result.total === null ? '--' : result.total}<small>分</small></div>
                <p>{result.feedback}</p>
                <div className="acupuncture-score-actions"><button className="acupuncture-secondary" onClick={onBack}><ArrowLeft size={16} /> 返回模式选择</button><button className="acupuncture-primary" onClick={restart}><RotateCcw size={16} /> 再练一次</button></div>
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
