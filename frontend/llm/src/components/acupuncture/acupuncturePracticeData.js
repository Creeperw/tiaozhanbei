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
        positionToleranceOuter: null,
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
            x: point.coordinates?.x ?? null,
            y: point.coordinates?.y ?? null,
            modelPosition: Array.isArray(point.modelPosition) ? point.modelPosition : null,
            depthRange: point.needleDepth,
            needleAngle: point.needleAngle || '',
            insertionType: point.insertionType ?? insertionTypeFromAngle(point.needleAngle),
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
            positionToleranceExcellent: source?.positionTolerance3d?.excellent ?? 0.012,
            positionTolerancePass: source?.positionTolerance3d?.pass ?? 0.03,
            positionToleranceOuter: source?.positionTolerance3d?.outer ?? 0.06,
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

export const BUILTIN_ACUPUNCTURE_CASES = [
    {
        caseId: 'acup_00001',
        title: '腕关节挫伤 — 太溪穴上病下取',
        patientInfo: {
            chiefComplaint: '右腕关节扭伤后疼痛10天，活动受限',
            historySummary: '右手着地致腕关节挫伤，X线片示无骨折，局部广泛压痛。',
        },
        cooperationWillingness: {
            patientLine: '医生，我这个手腕疼了十来天了，针灸真的能治好吗？',
            candidateOptions: [],
        },
        applicableRegions: ['feet'],
        regionImages: { feet: '脚部.png' },
        standardAcupoints: [{
            name: '太溪', code: 'KI3', pinyin: 'taixi', modelNodeName: 'taixi',
            procedureType: 'needling', insertionType: 'direct', region: 'feet', imagePath: '脚部.png',
            coordinates: { x: 0, y: 0, unit: 'px' },
            locationDescription: '足内侧，内踝后方，内踝尖与跟腱之间的凹陷处',
            needleDepth: { min: 0.5, max: 0.8, unit: '寸' },
            needleAngle: '直刺0.5-0.8寸', retentionTime: { min: 15, max: 25, unit: '分钟' },
        }],
    },
    ...[
        ['acup_00002', '膝关节痛（足太阴经证）— 隐白穴通经止痛', '隐白', 'SP1', '0.1-0.2'],
        ['acup_00003', '定时肩痛（卯时发作）— 三间穴子午流注针法', '三间', 'LI3', '0.3-0.5'],
        ['acup_00004', '急性腰扭伤（咳则加重）— 鱼际穴宣肺理气', '鱼际', 'LU10', '0.5-0.8'],
        ['acup_00005', '胸胁屏伤（咳引胸痛）— 鱼际穴宣肺通络', '鱼际', 'LU10', '0.5-0.8'],
        ['acup_00006', '急性荨麻疹 — 间使穴清热凉血', '间使', 'PC5', '0.8-1.2'],
        ['acup_00007', '颈性眩晕 — 涌泉穴引邪下行', '涌泉', 'KI1', '0.5-1.0'],
        ['acup_00008', '双小腿发凉（下肢寒痹）— 承山穴+委中刺络拔罐', '承山', 'BL57', '1.0-2.0'],
        ['acup_00009', '产后鸡爪风 — 液门+外关补气益阳', '液门', 'SJ2', '0.3-0.5'],
        ['acup_00010', '牙痛（上牙痛）— 液门穴清热止痛', '液门', 'SJ2', '0.5-1.0'],
    ].map(([caseId, title, name, code, depth]) => ({
        caseId,
        title,
        patientInfo: { chiefComplaint: title.split('—')[0] },
        cooperationWillingness: { patientLine: '医生，我愿意配合针灸训练。', candidateOptions: [] },
        applicableRegions: ['arms'],
        regionImages: { arms: '胳膊.png' },
        standardAcupoints: [{
            name, code, modelNodeName: name, region: 'arms', imagePath: '胳膊.png',
            coordinates: { x: 0, y: 0, unit: 'px' },
            locationDescription: '请根据体表标志完成定位',
            needleDepth: { min: Number(depth.split('-')[0]), max: Number(depth.split('-')[1]), unit: '寸' },
            needleAngle: '直刺',
            retentionTime: { min: 15, max: 30, unit: '分钟' },
        }],
    })),
];
