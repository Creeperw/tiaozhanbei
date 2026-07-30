export const scoreAcupunctureAttempt = (caseData, needles) => {
    const standards = Array.isArray(caseData?.standardPoints) ? caseData.standardPoints : [];
    const excellentTolerance = Number(caseData?.scoring?.positionToleranceExcellent);
    const passTolerance = Number(caseData?.scoring?.positionTolerancePass);
    const tolerance = Number(caseData?.scoring?.positionTolerancePercent);
    const depthRange = caseData?.scoring?.depthRange || caseData?.scoring?.depthRangeMm;
    const retentionRange = caseData?.scoring?.retentionRangeMinutes;
    const worldPositionConfigured = standards.length > 0
        && Number.isFinite(excellentTolerance) && Number.isFinite(passTolerance)
        && standards.every((standard) => Array.isArray(standard.modelPosition));
    const positionConfigured = worldPositionConfigured || (standards.length > 0 && Number.isFinite(tolerance)
        && standards.every((standard) => Number.isFinite(standard.x) && Number.isFinite(standard.y)));
    const positionTolerance = worldPositionConfigured ? passTolerance : tolerance;
    const insertionStandards = standards.filter((standard) => standard.insertionType);
    const insertionConfigured = insertionStandards.length > 0 && positionConfigured;
    const insertionHit = (needle) => {
        const matchedStandard = insertionStandards.find((standard) => {
            if (worldPositionConfigured) {
                if (!Array.isArray(needle.point)) return false;
                const distance = Math.hypot(...needle.point.map((value, index) => value - standard.modelPosition[index]));
                return distance <= positionTolerance;
            }
            return Math.hypot(needle.x - standard.x, needle.y - standard.y) <= tolerance;
        });
        return matchedStandard?.insertionType === needle.insertionType;
    };
    const depthConfigured = Array.isArray(depthRange);
    const retentionConfigured = Array.isArray(retentionRange);

    if (!positionConfigured && !depthConfigured && !retentionConfigured && !insertionConfigured) {
        return {
            available: false,
            total: null,
            position: null,
            depth: null,
            retention: null,
            feedback: '标准穴位、允许偏差、进针深度和留针时间尚未配置，当前操作已保留，暂不生成医学判定。',
        };
    }

    const positionHits = positionConfigured ? standards.filter((standard) => needles.some((needle) => {
        if (worldPositionConfigured) {
            if (!Array.isArray(needle.point)) return false;
            const distance = Math.hypot(...needle.point.map((value, index) => value - standard.modelPosition[index]));
            return distance <= positionTolerance;
        }
        return Math.hypot(needle.x - standard.x, needle.y - standard.y) <= tolerance;
    })).length : 0;
    const position = positionConfigured ? Math.round((positionHits / standards.length) * 100) : null;
    const depthHits = depthConfigured ? needles.filter((needle) => {
        const value = needle.depthValue ?? needle.depthMm;
        return value >= depthRange[0] && value <= depthRange[1];
    }).length : 0;
    const retentionHits = retentionConfigured ? needles.filter((needle) => needle.retentionMinutes >= retentionRange[0] && needle.retentionMinutes <= retentionRange[1]).length : 0;
    const depth = depthConfigured ? (needles.length ? Math.round((depthHits / needles.length) * 100) : 0) : null;
    const retention = retentionConfigured ? (needles.length ? Math.round((retentionHits / needles.length) * 100) : 0) : null;
    const insertionHits = insertionConfigured
        ? needles.filter(insertionHit).length
        : 0;
    const insertion = insertionConfigured ? (needles.length ? Math.round((insertionHits / needles.length) * 100) : 0) : null;
    const scoreParts = [position, depth, retention, insertion].filter((value) => value !== null);
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
        feedback: configuredFeedback || (total >= 85
            ? position === null
                ? '进针深度和留针时间已配置；坐标补充后即可生成位置判定。'
                : '定位与操作参数符合本病例标准。'
            : total >= 60
                ? '整体达到训练要求，仍可继续提高定位精度。'
                : '建议复习穴位定位、进针深度和留针时长后再次练习。'),
    };
};