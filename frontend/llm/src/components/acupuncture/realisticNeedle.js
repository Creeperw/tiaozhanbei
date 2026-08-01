import * as THREE from 'three';

const NEEDLE_IMAGE_URLS = {
    direct: '/acupuncture/needles/needle-direct.png',
    oblique: '/acupuncture/needles/needle-oblique.png',
};
const NEEDLE_INSERTION_TILTS = { direct: 0, oblique: 45, transverse: 75 };
const NEEDLE_IMAGE_LAYOUTS = {
    direct: {
        anchor: new THREE.Vector2(0.5, 0.115),
        scale: new THREE.Vector2(0.24, 0.36),
        imageAngle: Math.PI / 2,
    },
    oblique: {
        anchor: new THREE.Vector2(0.226, 0.227),
        scale: new THREE.Vector2(0.3, 0.45),
        imageAngle: Math.atan2(928, 575),
    },
};
const CONTACT_GLOW_GEOMETRY = new THREE.SphereGeometry(0.014, 16, 12);
const CONTACT_GLOW_MATERIAL = new THREE.MeshBasicMaterial({
    color: '#f2b35b', transparent: true, opacity: 0.42, depthWrite: false,
});
const needleTextureLoader = new THREE.TextureLoader();
const needleTextures = new Map();

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

function getNeedleTexture(insertionType, onLoad) {
    const url = getNeedleImageUrl(insertionType);
    const cached = needleTextures.get(url);
    if (cached) {
        if (onLoad) {
            if (cached.loaded) queueMicrotask(onLoad);
            else cached.callbacks.add(onLoad);
        }
        return cached.texture;
    }

    const callbacks = new Set(onLoad ? [onLoad] : []);
    const entry = { texture: null, loaded: false, callbacks };
    entry.texture = needleTextureLoader.load(url, (texture) => {
        texture.colorSpace = THREE.SRGBColorSpace;
        entry.loaded = true;
        entry.callbacks.forEach((callback) => callback());
        entry.callbacks.clear();
    });
    needleTextures.set(url, entry);
    return entry.texture;
}

function rotateAngledNeedleSprite(sprite, camera) {
    if (sprite.userData.insertionType === 'direct') {
        sprite.material.rotation = 0;
        return;
    }

    const cameraRight = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
    const cameraUp = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 1);
    const needlePoint = sprite.getWorldPosition(new THREE.Vector3());
    const cameraToNeedle = needlePoint.sub(camera.position).normalize();
    const projectedDirection = sprite.userData.direction.clone()
        .addScaledVector(cameraToNeedle, -sprite.userData.direction.dot(cameraToNeedle));
    if (projectedDirection.lengthSq() < 0.000001) return;

    projectedDirection.normalize();
    sprite.material.rotation = Math.atan2(
        projectedDirection.dot(cameraUp),
        projectedDirection.dot(cameraRight),
    ) - sprite.userData.imageAngle;
}

export function preloadNeedleTextures(onLoad) {
    const notify = () => onLoad?.();
    getNeedleTexture('direct', notify);
    getNeedleTexture('oblique', notify);
}

export function disposePlacedNeedles(group) {
    group.traverse((object) => {
        if (!object.isSprite || object.name !== 'needle-image') return;
        object.material.dispose();
    });
    group.clear();
}

export function createRealisticNeedle(needle, index = 0) {
    const insertionType = needle.insertionType === 'direct' ? 'direct' : 'oblique';
    const layout = NEEDLE_IMAGE_LAYOUTS[insertionType];
    const normal = new THREE.Vector3().fromArray(needle.normal || [0, 1, 0]).normalize();
    const tiltDeg = Number.isFinite(Number(needle.tiltAngle))
        ? Number(needle.tiltAngle)
        : NEEDLE_INSERTION_TILTS[needle.insertionType] ?? 0;
    const direction = getNeedleDirection(normal, tiltDeg, needle.directionAngle);
    const needleSprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: getNeedleTexture(insertionType),
        transparent: true,
        depthWrite: false,
    }));
    needleSprite.name = 'needle-image';
    needleSprite.center.copy(layout.anchor);
    needleSprite.scale.set(layout.scale.x, layout.scale.y, 1);
    needleSprite.userData.direction = direction.clone();
    needleSprite.userData.insertionType = insertionType;
    needleSprite.userData.imageAngle = layout.imageAngle;
    needleSprite.onBeforeRender = (_renderer, _scene, camera) => rotateAngledNeedleSprite(needleSprite, camera);

    const model = new THREE.Group();
    model.add(needleSprite);
    const contactGlow = new THREE.Mesh(CONTACT_GLOW_GEOMETRY, CONTACT_GLOW_MATERIAL);
    contactGlow.name = 'needle-contact-glow';
    contactGlow.renderOrder = 1;
    model.add(contactGlow);
    model.position.fromArray(needle.point);
    model.userData.needleId = needle.id || `needle-${index + 1}`;
    model.userData.direction = direction.toArray();
    return model;
}
