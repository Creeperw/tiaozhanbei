export const ACUPUNCTURE_REGION_OPTIONS = [
    { id: 'neck', label: '脖颈处' },
    { id: 'back', label: '背部' },
    { id: 'chest-abdomen', label: '胸腹部' },
    { id: 'arms', label: '手臂' },
    { id: 'legs', label: '腿部' },
    { id: 'feet', label: '足部' },
    { id: 'multi-region', label: '多部位联动' },
];

export const ACUPUNCTURE_REGION_IMAGES = {
    neck: '/acupuncture/head.png',
    back: '/acupuncture/back.png',
    'chest-abdomen': '/acupuncture/abdomen.png',
    arms: '/acupuncture/arms.png',
    legs: '/acupuncture/legs.png',
    feet: '/acupuncture/feet.png',
    'multi-region': '/acupuncture/body-front.jpg',
};

export const resolveRegionImage = (regionId, caseImages = {}) => (
    caseImages[regionId] || ACUPUNCTURE_REGION_IMAGES[regionId] || ''
);

export const getAcupunctureCaseDisplayTitle = (title = '') => {
    const normalizedTitle = String(title).trim();
    if (!normalizedTitle) return '未命名病例';
    return normalizedTitle.split('—')[0].trim() || normalizedTitle;
};

export const EMPTY_ACUPUNCTURE_CASE = {
    caseId: '',
    title: '待接入针灸病例',
    complaint: '',
    patientConsentPrompt: '医生建议进行针灸训练，患者是否愿意配合？',
    patientConsentReply: '',
    regionImages: {},
    standardPoints: [],
    scoring: {
        positionToleranceUnit: 'model',
        positionToleranceExcellent: null,
        positionTolerancePass: null,
        positionTolerancePercent: null,
        depthRange: null,
        depthUnit: null,
        depthRangeMm: null,
        retentionRangeMinutes: null,
    },
    feedback: {
        excellent: '',
        qualified: '',
        needsPractice: '',
    },
};

const ACUPUNCTURE_IMAGE_PATHS = {
    '胳膊.png': '/acupuncture/arms.png',
    '脚部.png': '/acupuncture/feet.png',
    '腿部.png': '/acupuncture/legs.png',
    '背部.png': '/acupuncture/back.png',
    '腹部.png': '/acupuncture/abdomen.png',
    '头部.png': '/acupuncture/head.png',
    '正面全部.jpg': '/acupuncture/body-front.jpg',
    '后面全部.jpg': '/acupuncture/body-back.jpg',
    '正面部.jpg': '/acupuncture/body-front-detail.jpg',
    '正面（头-腹部）.jpg': '/acupuncture/body-head-abdomen.jpg',
    '背部.jpg': '/acupuncture/back-detail.jpg',
};

const resolveAcupunctureImage = (value) => {
    if (!value) return '';
    return value.startsWith('/') ? value : ACUPUNCTURE_IMAGE_PATHS[value] || `/acupuncture/${value}`;
};

const CONSENT_OPTION_TEXT = {
    correct: '我理解您的担心，会先耐心听您说明顾虑，解释训练流程和注意事项，确认您愿意后再继续。',
    incorrect: [
        '别紧张，这只是小问题，不用多问，我们直接开始就行。',
        '如果您有顾虑，就先不用沟通，等您自己想清楚再说。',
    ],
};

const normalizeConsentOptions = (options) => {
    if (!Array.isArray(options) || !options.length) return [];
    let incorrectIndex = 0;
    return options.map((option) => {
        if (option.isCorrect) {
            return { ...option, text: CONSENT_OPTION_TEXT.correct };
        }
        const text = CONSENT_OPTION_TEXT.incorrect[Math.min(incorrectIndex, CONSENT_OPTION_TEXT.incorrect.length - 1)];
        incorrectIndex += 1;
        return { ...option, text };
    });
};

const insertionTypeFromAngle = (angle) => {
    const text = String(angle || '');
    if (text.includes('直刺')) return 'direct';
    if (text.includes('斜刺')) return 'oblique';
    if (text.includes('平刺') || text.includes('横刺')) return 'transverse';
    return undefined;
};

export const normalizeAcupunctureCase = (source) => {
    const standardPoints = Array.isArray(source?.standardAcupoints)
        ? source.standardAcupoints.map((point) => ({
            ...point,
            id: point.code || point.name,
            regionId: point.region,
            x: point.coordinates?.x || null,
            y: point.coordinates?.y || null,
            depthRange: point.needleDepth,
            needleAngle: point.needleAngle || '',
            insertionType: insertionTypeFromAngle(point.needleAngle),
            retentionRange: point.retentionTime,
        }))
        : [];
    const firstPoint = standardPoints[0];
    const hasCoordinates = standardPoints.length > 0
        && standardPoints.every((point) => Number.isFinite(point.x) && Number.isFinite(point.y)
            && !(point.x === 0 && point.y === 0));

    return {
        ...EMPTY_ACUPUNCTURE_CASE,
        caseId: source?.caseId || '',
        title: source?.title || EMPTY_ACUPUNCTURE_CASE.title,
        complaint: source?.patientInfo?.chiefComplaint || '',
        historySummary: source?.patientInfo?.historySummary || '',
        patientInfo: source?.patientInfo || {},
        patientConsentPrompt: source?.cooperationWillingness?.patientLine || EMPTY_ACUPUNCTURE_CASE.patientConsentPrompt,
        consentOptions: normalizeConsentOptions(source?.cooperationWillingness?.candidateOptions),
        acceptanceOutcome: source?.cooperationWillingness?.acceptanceOutcome ?? null,
        applicableRegions: source?.applicableRegions || [],
        regionImages: Object.fromEntries(
            Object.entries(source?.regionImages || {}).map(([region, image]) => [region, resolveAcupunctureImage(image)]),
        ),
        standardPoints,
        scoring: {
            positionToleranceUnit: source?.standardAcupoints?.[0]?.positionTolerance?.unit || 'model',
            positionToleranceExcellent: source?.positionTolerance3d?.excellent ?? 0.005,
            positionTolerancePass: source?.positionTolerance3d?.pass ?? 0.01,
            positionTolerancePercent: hasCoordinates ? source?.positionTolerance?.value : null,
            depthRange: firstPoint?.depthRange
                && Number.isFinite(firstPoint.depthRange.min)
                && Number.isFinite(firstPoint.depthRange.max)
                ? [firstPoint.depthRange.min, firstPoint.depthRange.max]
                : null,
            depthUnit: firstPoint?.depthRange?.unit || null,
            depthRangeMm: firstPoint?.depthRange?.unit === 'mm'
                ? [firstPoint.depthRange.min, firstPoint.depthRange.max]
                : null,
            retentionRangeMinutes: firstPoint?.retentionRange?.unit === '分钟'
                ? [firstPoint.retentionRange.min, firstPoint.retentionRange.max]
                : null,
        },
        feedback: {
            excellent: source?.feedbackScripts?.excellent?.message || '',
            qualified: source?.feedbackScripts?.pass?.message || '',
            needsPractice: source?.feedbackScripts?.needsImprovement?.message || '',
        },
        source: source?.source || {},
    };
};