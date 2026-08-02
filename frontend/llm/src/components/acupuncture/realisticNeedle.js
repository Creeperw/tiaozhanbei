import * as THREE from 'three';

const NEEDLE_IMAGE_URLS = {
    direct: '/acupuncture/needles/needle-direct.png',
    oblique: '/acupuncture/needles/needle-oblique.png',
};
const NEEDLE_INSERTION_TILTS = { direct: 0, oblique: 45, transverse: 75 };
const NEEDLE_AXIS = new THREE.Vector3(0, 1, 0);
const NEEDLE_MODEL_SCALE = 0.75;
const NEEDLE_FRONT_RADIUS_SCALE = 0.5;
const NEEDLE_SHAFT_MATERIAL = new THREE.MeshPhysicalMaterial({
    color: '#d9dee2',
    metalness: 0.96,
    roughness: 0.2,
    clearcoat: 0.35,
    clearcoatRoughness: 0.16,
});
const NEEDLE_GRIP_MATERIAL = new THREE.MeshPhysicalMaterial({
    color: '#c7834e',
    metalness: 0.9,
    roughness: 0.24,
    clearcoat: 0.28,
    clearcoatRoughness: 0.2,
});
const CONTACT_GLOW_GEOMETRY = new THREE.SphereGeometry(0.014, 16, 12);
const CONTACT_GLOW_MATERIAL = new THREE.MeshBasicMaterial({
    color: '#f2b35b', transparent: true, opacity: 0.42, depthWrite: false,
});

let fallbackNeedleTemplate = null;
let realisticNeedleTemplate = null;

function isWarmGripPart(name) {
    return name === 'needle-handle'
        || name === 'needle-grip-ring'
        || name === 'needle-loop';
}

function applyReferenceNeedleMaterials(root) {
    root.traverse((object) => {
        if (!object.isMesh) return;
        object.material = isWarmGripPart(object.name)
            ? NEEDLE_GRIP_MATERIAL
            : NEEDLE_SHAFT_MATERIAL;
    });
}

function applyNeedleDimensions(model) {
    model.scale.multiplyScalar(NEEDLE_MODEL_SCALE);
    model.traverse((object) => {
        if (object.name !== 'needle-shaft' && object.name !== 'needle-tip') return;
        object.scale.x *= NEEDLE_FRONT_RADIUS_SCALE;
        object.scale.z *= NEEDLE_FRONT_RADIUS_SCALE;
    });
}

export function getNeedleImageUrl(insertionType) {
    return insertionType === 'direct' ? NEEDLE_IMAGE_URLS.direct : NEEDLE_IMAGE_URLS.oblique;
}

export function getNeedleDirection(normal, tiltDeg = 0, directionDeg = 0) {
    const surfaceNormal = normal.clone().normalize();
    const reference = Math.abs(surfaceNormal.y) < 0.9
        ? new THREE.Vector3(0, 1, 0)
        : new THREE.Vector3(1, 0, 0);
    const tangent = new THREE.Vector3().crossVectors(reference, surfaceNormal).normalize();
    const bitangent = new THREE.Vector3().crossVectors(surfaceNormal, tangent).normalize();
    const tilt = THREE.MathUtils.degToRad(Number(tiltDeg) || 0);
    const azimuth = THREE.MathUtils.degToRad(Number(directionDeg) || 0);

    return surfaceNormal.multiplyScalar(Math.cos(tilt))
        .add(tangent.multiplyScalar(Math.sin(tilt) * Math.cos(azimuth)))
        .add(bitangent.multiplyScalar(Math.sin(tilt) * Math.sin(azimuth)))
        .normalize();
}

function createFallbackNeedleTemplate() {
    const needle = new THREE.Group();
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.24, 12), NEEDLE_SHAFT_MATERIAL);
    shaft.name = 'needle-shaft';
    shaft.position.y = 0.12;
    needle.add(shaft);

    const tip = new THREE.Mesh(new THREE.ConeGeometry(0.003, 0.012, 12), NEEDLE_SHAFT_MATERIAL);
    tip.name = 'needle-tip';
    tip.position.y = -0.006;
    needle.add(tip);

    const handle = new THREE.Mesh(new THREE.CylinderGeometry(0.015, 0.015, 0.072, 18), NEEDLE_GRIP_MATERIAL);
    handle.name = 'needle-handle';
    handle.position.y = 0.276;
    needle.add(handle);

    for (let index = 0; index < 10; index += 1) {
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.0157, 0.00155, 6, 18), NEEDLE_GRIP_MATERIAL);
        ring.name = 'needle-grip-ring';
        ring.rotation.x = Math.PI / 2;
        ring.position.y = 0.245 + index * 0.0069;
        needle.add(ring);
    }

    const loop = new THREE.Mesh(new THREE.TorusGeometry(0.014, 0.0018, 8, 20), NEEDLE_GRIP_MATERIAL);
    loop.name = 'needle-loop';
    loop.rotation.y = Math.PI / 2;
    loop.scale.y = 1.35;
    loop.position.y = 0.335;
    needle.add(loop);
    return needle;
}

export function cacheRealisticNeedleTemplate(scene) {
    if (realisticNeedleTemplate) {
        disposeNeedleScene(scene);
        return false;
    }
    realisticNeedleTemplate = scene.children.find((child) => child.name === 'realistic-acupuncture-needle') || scene;
    realisticNeedleTemplate.traverse((object) => {
        if (/^needle-grip-ring(?:_\d+)?$/.test(object.name)) object.name = 'needle-grip-ring';
    });
    applyReferenceNeedleMaterials(realisticNeedleTemplate);
    return true;
}

export function disposeNeedleScene(scene) {
    const geometries = new Set();
    const materials = new Set();
    scene.traverse((object) => {
        if (!object.isMesh) return;
        geometries.add(object.geometry);
        (Array.isArray(object.material) ? object.material : [object.material]).forEach((material) => materials.add(material));
    });
    geometries.forEach((geometry) => geometry?.dispose());
    materials.forEach((material) => material?.dispose());
}

export function disposePlacedNeedles(group) {
    group.clear();
}

export function createRealisticNeedle(needle, index = 0) {
    fallbackNeedleTemplate ||= createFallbackNeedleTemplate();
    const model = (realisticNeedleTemplate || fallbackNeedleTemplate).clone(true);
    const point = new THREE.Vector3(...needle.point);
    const normal = new THREE.Vector3(...(needle.normal || [0, 1, 0])).normalize();
    const tiltDeg = Number.isFinite(Number(needle.tiltAngle))
        ? Number(needle.tiltAngle)
        : NEEDLE_INSERTION_TILTS[needle.insertionType] ?? 0;
    const direction = getNeedleDirection(normal, tiltDeg, needle.directionAngle);
    const contactGlow = new THREE.Mesh(CONTACT_GLOW_GEOMETRY, CONTACT_GLOW_MATERIAL);
    contactGlow.name = 'needle-contact-glow';
    contactGlow.renderOrder = 1;
    model.add(contactGlow);
    applyNeedleDimensions(model);
    model.position.copy(point);
    model.quaternion.setFromUnitVectors(NEEDLE_AXIS, direction);
    model.userData.needleId = needle.id || `needle-${index + 1}`;
    model.userData.direction = direction.toArray();
    return model;
}
