import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import {
    createRealisticNeedle,
    getNeedleDirection,
} from './realisticNeedle';

describe('realistic acupuncture needle', () => {
    it('builds a silver needle group with all reference features', () => {
        const needle = createRealisticNeedle({
            id: 'needle-1',
            point: [1, 2, 3],
            normal: [0, 1, 0],
            insertionType: 'direct',
        });

        expect(needle).toBeInstanceOf(THREE.Group);
        expect(needle.userData.needleId).toBe('needle-1');
        expect(needle.children.find((child) => child.name === 'needle-shaft')).toBeTruthy();
        expect(needle.children.find((child) => child.name === 'needle-tip')).toBeTruthy();
        expect(needle.children.find((child) => child.name === 'needle-handle')).toBeTruthy();
        expect(needle.children.find((child) => child.name === 'needle-loop')).toBeTruthy();
        expect(needle.children.find((child) => child.name === 'needle-contact-glow')).toBeTruthy();
        expect(needle.children.filter((child) => child.name === 'needle-grip-ring')).toHaveLength(10);
    });

    it('keeps 0, 45, and 75 degree tilts at their intended angle to the surface normal', () => {
        const normal = new THREE.Vector3(0, 1, 0);

        expect(getNeedleDirection(normal, 0, 0).angleTo(normal)).toBeCloseTo(0);
        expect(getNeedleDirection(normal, 45, 0).angleTo(normal)).toBeCloseTo(Math.PI / 4);
        expect(getNeedleDirection(normal, 75, 0).angleTo(normal))
            .toBeCloseTo(THREE.MathUtils.degToRad(75));
    });

    it('reuses the fallback silver model and contact-glow resources across placements', () => {
        const firstNeedle = createRealisticNeedle({ id: 'needle-1', point: [0, 0, 0] });
        const secondNeedle = createRealisticNeedle({ id: 'needle-2', point: [1, 0, 0] });
        const firstShaft = firstNeedle.children.find((child) => child.name === 'needle-shaft');
        const secondShaft = secondNeedle.children.find((child) => child.name === 'needle-shaft');
        const firstGlow = firstNeedle.children.find((child) => child.name === 'needle-contact-glow');
        const secondGlow = secondNeedle.children.find((child) => child.name === 'needle-contact-glow');

        expect(firstShaft.geometry).toBe(secondShaft.geometry);
        expect(firstShaft.material).toBe(secondShaft.material);
        expect(firstGlow.geometry).toBe(secondGlow.geometry);
        expect(firstGlow.material).toBe(secondGlow.material);
    });
});
