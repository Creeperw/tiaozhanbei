import * as THREE from 'three';
import { describe, expect, it, vi } from 'vitest';

import { HUMAN_MODEL_URL } from './AcupunctureModelCanvas';
import {
    createRealisticNeedle,
    disposePlacedNeedles,
    getNeedleDirection,
    getNeedleImageUrl,
} from './realisticNeedle';

describe('image acupuncture needle', () => {
    it('loads the human model from its public Vite path', () => {
        expect(HUMAN_MODEL_URL).toBe('/blender.yibiaozhu.glb');
    });

    it('selects the direct reference image for direct insertion', () => {
        expect(getNeedleImageUrl('direct')).toBe('/acupuncture/needles/needle-direct.png');
    });

    it('selects the oblique reference image for oblique, transverse, and unknown insertion', () => {
        expect(getNeedleImageUrl('oblique')).toBe('/acupuncture/needles/needle-oblique.png');
        expect(getNeedleImageUrl('transverse')).toBe('/acupuncture/needles/needle-oblique.png');
        expect(getNeedleImageUrl('unknown')).toBe('/acupuncture/needles/needle-oblique.png');
    });

    it('keeps a 45 degree tilt at its intended angle to the surface normal regardless of azimuth', () => {
        const normal = new THREE.Vector3(0, 1, 0);

        expect(getNeedleDirection(normal, 45, 0).angleTo(normal)).toBeCloseTo(Math.PI / 4);
        expect(getNeedleDirection(normal, 45, 90).angleTo(normal)).toBeCloseTo(Math.PI / 4);
    });

    it('records an angled insertion direction while anchoring the image needle and glow at its point', () => {
        const needle = {
            id: 'needle-oblique',
            point: [1, 2, 3],
            normal: [0, 1, 0],
            insertionType: 'oblique',
            tiltAngle: 45,
            directionAngle: 90,
        };

        const model = createRealisticNeedle(needle);
        const expectedDirection = getNeedleDirection(new THREE.Vector3(...needle.normal), 45, 90);

        expect(model.position.toArray()).toEqual(needle.point);
        expect(model.userData.direction).toEqual(expectedDirection.toArray());
        expect(model.children.find((child) => child.name === 'needle-image').position.toArray()).toEqual([0, 0, 0]);
        expect(model.children.find((child) => child.name === 'needle-contact-glow').position.toArray()).toEqual([0, 0, 0]);
    });

    it('disposes each placed needle sprite material before clearing its group', () => {
        const group = new THREE.Group();
        const material = new THREE.SpriteMaterial();
        const dispose = vi.spyOn(material, 'dispose');
        const needleSprite = new THREE.Sprite(material);
        needleSprite.name = 'needle-image';
        group.add(needleSprite);

        disposePlacedNeedles(group);

        expect(dispose).toHaveBeenCalledTimes(1);
        expect(group.children).toHaveLength(0);
    });
});
