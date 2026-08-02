const finiteNumber = (value) => (
    value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
        ? null
        : Number(value)
);

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

const getPositionTolerances = (standard, scoring, worldPositionConfigured) => {
    const configuredPass = finiteNumber(
        standard?.positionTolerance?.pass
        ?? (worldPositionConfigured ? scoring.positionTolerancePass : scoring.positionTolerancePercent),
    );
    if (configuredPass === null || configuredPass <= 0) return null;
    const configuredExcellent = finiteNumber(
        standard?.positionTolerance?.excellent ?? scoring.positionToleranceExcellent,
    );
    const configuredOuter = finiteNumber(
        standard?.positionTolerance?.outer ?? scoring.positionToleranceOuter,
    );
    return {
        excellent: configuredExcellent !== null && configuredExcellent > 0
            ? Math.min(configuredExcellent, configuredPass)
            : configuredPass * 0.5,
        pass: configuredPass,
        outer: configuredOuter !== null && configuredOuter > configuredPass
            ? configuredOuter
            : configuredPass * 2,
    };
};

const scorePositionDistance = (distance, tolerance) => {
    if (distance === null || !tolerance) return 0;
    if (distance <= tolerance.excellent) return 100;
    if (distance <= tolerance.pass) {
        const progress = (distance - tolerance.excellent) / (tolerance.pass - tolerance.excellent);
        return Math.round(100 - progress * 40);
    }
    if (distance <= tolerance.outer) {
        const progress = (distance - tolerance.pass) / (tolerance.outer - tolerance.pass);
        return Math.round(60 - progress * 60);
    }
    return 0;
};

const matchNeedlesToStandards = (standards, needles, scoring, worldPositionConfigured) => {
    const candidates = [];
    standards.forEach((standard, standardIndex) => {
        needles.forEach((needle, needleIndex) => {
            const distance = getDistance(needle, standard, worldPositionConfigured);
            if (distance !== null) candidates.push({ standard, standardIndex, needle, needleIndex, distance });
        });
    });
    candidates.sort((first, second) => first.distance - second.distance);

    const matches = [];
    const usedStandardIndexes = new Set();
    const usedNeedleIndexes = new Set();
    candidates.forEach((candidate) => {
        if (usedStandardIndexes.has(candidate.standardIndex) || usedNeedleIndexes.has(candidate.needleIndex)) return;
        usedStandardIndexes.add(candidate.standardIndex);
        usedNeedleIndexes.add(candidate.needleIndex);
        matches.push({
            ...candidate,
            positionScore: scorePositionDistance(
                candidate.distance,
                getPositionTolerances(candidate.standard, scoring, worldPositionConfigured),
            ),
        });
    });
    return matches;
};

const scoreMatchedRange = (standards, matches, field, readValue, fallbackRange) => {
    const configured = standards
        .map((standard, standardIndex) => {
            const ownRange = standard?.[field];
            const range = ownRange || (Array.isArray(fallbackRange)
                ? { min: fallbackRange[0], max: fallbackRange[1] }
                : null);
            return { standard, standardIndex, range };
        })
        .filter(({ range }) => finiteNumber(range?.min) !== null && finiteNumber(range?.max) !== null);
    if (!configured.length) return null;
    const hits = configured.filter(({ standardIndex, range }) => {
        const match = matches.find((item) => item.standardIndex === standardIndex);
        const value = readValue(match?.needle);
        return value !== null && value >= Number(range.min) && value <= Number(range.max);
    }).length;
    return Math.round((hits / configured.length) * 100);
};

export const scoreAcupunctureAttempt = (caseData, needles) => {
    const allStandards = Array.isArray(caseData?.standardPoints) ? caseData.standardPoints : [];
    const standards = allStandards.filter((standard) => !['pricking', 'pricking_cupping'].includes(standard.procedureType));
    const attempts = Array.isArray(needles) ? needles : [];
    const scoring = caseData?.scoring || {};
    const worldTolerance = finiteNumber(scoring.positionTolerancePass);
    const coordinateTolerance = finiteNumber(scoring.positionTolerancePercent);
    const worldPositionConfigured = standards.length > 0
        && worldTolerance !== null
        && standards.every((standard) => Array.isArray(standard.modelPosition));
    const coordinatePositionConfigured = standards.length > 0
        && coordinateTolerance !== null
        && standards.every((standard) => Number.isFinite(standard.x) && Number.isFinite(standard.y));
    const positionConfigured = worldPositionConfigured || coordinatePositionConfigured;
    const matches = positionConfigured
        ? matchNeedlesToStandards(standards, attempts, scoring, worldPositionConfigured)
        : [];
    const parameterMatches = positionConfigured
        ? matches
        : standards.slice(0, attempts.length).map((standard, standardIndex) => ({
            standard,
            standardIndex,
            needle: attempts[standardIndex],
            needleIndex: standardIndex,
            positionScore: 0,
        }));
    const position = positionConfigured && standards.length
        ? Math.round(matches.reduce((sum, match) => sum + match.positionScore, 0)
            / Math.max(standards.length, attempts.length, 1))
        : null;

    const depth = scoreMatchedRange(
        standards,
        parameterMatches,
        'depthRange',
        getDepthValue,
        scoring.depthRange || scoring.depthRangeMm,
    );
    const retention = scoreMatchedRange(
        standards,
        parameterMatches,
        'retentionRange',
        getRetentionValue,
        scoring.retentionRangeMinutes,
    );

    const insertionStandards = standards.filter((standard) => standard.insertionType);
    const insertionMatches = matches.filter((match) => (
        match.positionScore > 0 && match.standard.insertionType === match.needle.insertionType
    )).length;
    const insertion = positionConfigured && insertionStandards.length
        ? Math.round((insertionMatches / insertionStandards.length) * 100)
        : null;

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
