const finiteNumber = (value) => Number.isFinite(Number(value)) ? Number(value) : null;

const getDepthValue = (needle) => finiteNumber(needle?.depthValue ?? needle?.depthMm ?? needle?.depth);
const getRetentionValue = (needle) => finiteNumber(needle?.retentionMinutes ?? needle?.retentionTime);

const getDistance = (needle, standard, worldPositionConfigured) => {
    if (worldPositionConfigured) {
        if (!Array.isArray(needle?.point) || !Array.isArray(standard?.modelPosition)) return null;
        return Math.hypot(...needle.point.map((value, index) => value - standard.modelPosition[index]));
    }
    if (!Number.isFinite(needle?.x) || !Number.isFinite(needle?.y)
        || !Number.isFinite(standard?.x) || !Number.isFinite(standard?.y)) return null;
    return Math.hypot(needle.x - standard.x, needle.y - standard.y);
};

const matchNeedlesToStandards = (standards, needles, tolerance, worldPositionConfigured) => {
    const matches = [];
    const usedNeedleIndexes = new Set();
    standards.forEach((standard, standardIndex) => {
        let best = null;
        needles.forEach((needle, needleIndex) => {
            if (usedNeedleIndexes.has(needleIndex)) return;
            const distance = getDistance(needle, standard, worldPositionConfigured);
            if (distance === null || distance > tolerance || (best && distance >= best.distance)) return;
            best = { standard, standardIndex, needle, needleIndex, distance };
        });
        if (best) {
            usedNeedleIndexes.add(best.needleIndex);
            matches.push(best);
        }
    });
    return matches;
};

export const scoreAcupunctureAttempt = (caseData, needles) => {
    const standards = Array.isArray(caseData?.standardPoints) ? caseData.standardPoints : [];
    const attempts = Array.isArray(needles) ? needles : [];
    const scoring = caseData?.scoring || {};
    const firstStandard = standards[0];
    const depthRange = scoring.depthRange || scoring.depthRangeMm
        || (firstStandard?.depthRange
            && [finiteNumber(firstStandard.depthRange.min), finiteNumber(firstStandard.depthRange.max)]);
    const retentionRange = scoring.retentionRangeMinutes
        || (firstStandard?.retentionRange
            && [finiteNumber(firstStandard.retentionRange.min), finiteNumber(firstStandard.retentionRange.max)]);
    const worldTolerance = finiteNumber(scoring.positionTolerancePass);
    const coordinateTolerance = finiteNumber(scoring.positionTolerancePercent);
    const worldPositionConfigured = standards.length > 0
        && worldTolerance !== null
        && standards.every((standard) => Array.isArray(standard.modelPosition));
    const coordinatePositionConfigured = standards.length > 0
        && coordinateTolerance !== null
        && standards.every((standard) => Number.isFinite(standard.x) && Number.isFinite(standard.y));
    const positionConfigured = worldPositionConfigured || coordinatePositionConfigured;
    const positionTolerance = worldPositionConfigured ? worldTolerance : coordinateTolerance;
    const matches = positionConfigured
        ? matchNeedlesToStandards(standards, attempts, positionTolerance, worldPositionConfigured)
        : [];
    const position = positionConfigured && standards.length
        ? Math.round((matches.length / standards.length) * 100)
        : null;

    const depthConfigured = Array.isArray(depthRange)
        && depthRange.length === 2
        && depthRange.every((value) => value !== null);
    const retentionConfigured = Array.isArray(retentionRange)
        && retentionRange.length === 2
        && retentionRange.every((value) => value !== null);
    const evaluatedNeedles = attempts;
    const depthMatches = evaluatedNeedles.filter((needle) => {
        const value = getDepthValue(needle);
        return depthConfigured && value !== null && value >= depthRange[0] && value <= depthRange[1];
    }).length;
    const retentionMatches = evaluatedNeedles.filter((needle) => {
        const value = getRetentionValue(needle);
        return retentionConfigured && value !== null && value >= retentionRange[0] && value <= retentionRange[1];
    }).length;
    const measuredNeedles = evaluatedNeedles.length;
    const depth = depthConfigured ? (measuredNeedles ? Math.round((depthMatches / measuredNeedles) * 100) : 0) : null;
    const retention = retentionConfigured ? (measuredNeedles ? Math.round((retentionMatches / measuredNeedles) * 100) : 0) : null;

    const insertionStandards = standards.filter((standard) => standard.insertionType);
    const insertionConfigured = positionConfigured && insertionStandards.length > 0;
    const insertionMatches = matches.filter((match) => match.standard.insertionType === match.needle.insertionType).length;
    const insertion = insertionConfigured ? Math.round((insertionMatches / insertionStandards.length) * 100) : null;

    const scoreParts = [position, depth, retention, insertion].filter((value) => value !== null);
    if (!scoreParts.length) {
        return {
            available: false, total: null, position: null, depth: null, retention: null, insertion: null,
            feedback: '当前没有可判定的用户针次或标准数据，请完成施针后再评分。',
        };
    }
    const total = Math.round(scoreParts.reduce((sum, value) => sum + value, 0) / scoreParts.length);
    const configuredFeedback = total >= 85
        ? caseData.feedback?.excellent
        : total >= 60
            ? caseData.feedback?.qualified
            : caseData.feedback?.needsPractice;

    return {
        available: true,
        total,
        position,
        depth,
        retention,
        insertion,
        feedback: configuredFeedback || (total >= 85 ? '定位与操作参数符合本病例标准。' : total >= 60
            ? '整体达到训练要求，仍可继续提高定位精度。'
            : '建议复习穴位定位、进针深度和留针时长后再次练习。'),
    };
};
