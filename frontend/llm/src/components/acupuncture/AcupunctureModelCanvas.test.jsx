import * as THREE from 'three';
import { describe, expect, it } from 'vitest';

import { HUMAN_MODEL_URL } from './AcupunctureModelCanvas';
import {
    createRealisticNeedle,
    disposePlacedNeedles,
    getNeedleDirection,
    getNeedleImageUrl,
} from './realisticNeedle';

describe('image acupuncture needle', () => {
    it('loads the human model from the backend acupuncture static mount', () => {
        expect(HUMAN_MODEL_URL).toBe('/acupuncture-models/blender.yibiaozhu.glb');
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

    it('aligns a direct needle axis to the picked skin normal instead of world vertical', () => {
        const normal = new THREE.Vector3(0.6, 0.8, 0).normalize();
        const model = createRealisticNeedle({
            id: 'direct-on-side',
            point: [0, 0, 0],
            normal: normal.toArray(),
            insertionType: 'direct',
            tiltAngle: 0,
        });
        const needleAxis = new THREE.Vector3(0, 1, 0).applyQuaternion(model.quaternion);

        expect(needleAxis.angleTo(normal)).toBeCloseTo(0);
        expect(model.children.find((child) => child.name === 'needle-shaft')).toBeTruthy();
    });

    it('records an angled insertion direction while anchoring the 3D needle and glow at its point', () => {
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
        const needleAxis = new THREE.Vector3(0, 1, 0).applyQuaternion(model.quaternion);

        expect(model.position.toArray()).toEqual(needle.point);
        expect(model.userData.direction).toEqual(expectedDirection.toArray());
        expect(needleAxis.angleTo(expectedDirection)).toBeCloseTo(0);
        expect(model.children.find((child) => child.name === 'needle-shaft')).toBeTruthy();
        expect(model.children.find((child) => child.name === 'needle-contact-glow').position.toArray()).toEqual([0, 0, 0]);
    });

    it('clears placed 3D needle instances without disposing their shared template materials', () => {
        const group = new THREE.Group();
        group.add(new THREE.Mesh(new THREE.CylinderGeometry(), new THREE.MeshPhysicalMaterial()));

        disposePlacedNeedles(group);

        expect(group.children).toHaveLength(0);
    });
});
