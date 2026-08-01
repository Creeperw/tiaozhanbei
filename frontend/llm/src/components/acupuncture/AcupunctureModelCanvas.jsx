import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
    createRealisticNeedle,
    disposePlacedNeedles,
    preloadNeedleTextures,
} from './realisticNeedle';

const EXCLUDED_NODE_NAMES = new Set([
    'huantiao.032',
    'huantiao032',
    '空物体.016',
    '空物体016',
    'Sketchfab_model',
    'root',
    'GLTF_SceneRootNode',
    'BaseSpiderMan_0',
]);

const isMarkerNode = (object) => object.name
    && !EXCLUDED_NODE_NAMES.has(object.name)
    && !object.isMesh
    && !object.isCamera
    && !object.isLight
    && !object.isBone;

const NEEDLE_TAP_THRESHOLD_PX = 6;
export const HUMAN_MODEL_URL = '/blender.yibiaozhu.glb';

export default function AcupunctureModelCanvas({
    mode = 'observe',
    showMarkers = false,
    highlightNames = [],
    standardNodeNames = [],
    expanded = false,
    fullScreen = false,
    onStandardPointsReady,
    onSurfacePick,
    onToggleMarkers,
    needles = [],
}) {
    const interactive = mode === 'needling';
    const revealStandardPoints = mode === 'review';
    const hostRef = useRef(null);
    const sceneRef = useRef(null);
    const markerGroupRef = useRef(null);
    const standardMarkerGroupRef = useRef(null);
    const needleGroupRef = useRef(null);
    const modelRef = useRef(null);
    const raycasterRef = useRef(new THREE.Raycaster());
    const pointerRef = useRef(new THREE.Vector2());
    const pointNameMapRef = useRef({});
    const [status, setStatus] = useState('正在加载 3D 模型…');
    const [modelStats, setModelStats] = useState({ markers: 0 });
    const [showPointNames, setShowPointNames] = useState(false);
    const [hoveredPoint, setHoveredPoint] = useState(null);
    const [needleTextureVersion, setNeedleTextureVersion] = useState(0);
    const pendingPickRef = useRef(null);
    const pointerGestureRef = useRef(null);
    const markerVisibilityRef = useRef({ showMarkers, revealStandardPoints });

    useEffect(() => {
        markerVisibilityRef.current = { showMarkers, revealStandardPoints };
        if (markerGroupRef.current) markerGroupRef.current.visible = showMarkers;
        if (standardMarkerGroupRef.current) {
            standardMarkerGroupRef.current.visible = showMarkers || revealStandardPoints;
        }
    }, [revealStandardPoints, showMarkers]);

    useEffect(() => {
        let active = true;
        preloadNeedleTextures(() => {
            if (active) setNeedleTextureVersion((version) => version + 1);
        });
        return () => { active = false; };
    }, []);

    useEffect(() => {
        let cancelled = false;
        fetch('/acupuncture/acupoint-name-map.csv')
            .then((response) => response.ok ? response.text() : '')
            .then((text) => {
                if (cancelled) return;
                const map = {};
                text.split(/\r?\n/).slice(1).forEach((line) => {
                    const columns = line.split(',').map((value) => value.replace(/^"|"$/g, '').trim());
                    const pinyin = columns[0];
                    const chineseName = columns[1];
                    const nodeNames = columns[2]?.split(/[;|\s]+/).filter(Boolean) || [];
                    if (chineseName) [pinyin, ...nodeNames].filter(Boolean).forEach((name) => { map[name] = chineseName; });
                });
                pointNameMapRef.current = map;
                [...(markerGroupRef.current?.children || []), ...(standardMarkerGroupRef.current?.children || [])]
                    .forEach((marker) => {
                        marker.userData.pointName = map[marker.userData.nodeName] || marker.userData.nodeName;
                    });
            })
            .catch(() => { });
        return () => { cancelled = true; };
    }, []);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return undefined;
        const scene = new THREE.Scene();
        sceneRef.current = scene;

        const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
        host.__acupunctureCamera = camera;
        let renderer;
        try {
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        } catch {
            queueMicrotask(() => setStatus('当前环境不支持 WebGL，请使用支持 3D 加速的浏览器'));
            return () => {
                delete host.__acupunctureCamera;
                scene.clear();
            };
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.setClearColor(0x000000, 0);
        host.replaceChildren(renderer.domElement);

        scene.add(new THREE.HemisphereLight('#f6f3eb', '#15201e', 2.4));
        const keyLight = new THREE.DirectionalLight('#ffffff', 3.2);
        keyLight.position.set(4, 7, 5);
        scene.add(keyLight);
        const fillLight = new THREE.DirectionalLight('#b8d2cd', 1.15);
        fillLight.position.set(-5, 2, -4);
        scene.add(fillLight);
        const rimLight = new THREE.DirectionalLight('#ffe0b2', 2.15);
        rimLight.position.set(-4, 5, -6);
        scene.add(rimLight);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.minDistance = 0.05;
        controls.maxDistance = 100;

        const markerGroup = new THREE.Group();
        markerGroup.visible = markerVisibilityRef.current.showMarkers;
        markerGroupRef.current = markerGroup;
        scene.add(markerGroup);
        const standardMarkerGroup = new THREE.Group();
        standardMarkerGroup.visible = markerVisibilityRef.current.showMarkers
            || markerVisibilityRef.current.revealStandardPoints;
        standardMarkerGroupRef.current = standardMarkerGroup;
        scene.add(standardMarkerGroup);
        const needleGroup = new THREE.Group();
        needleGroupRef.current = needleGroup;
        scene.add(needleGroup);

        let disposed = false;
        let animationFrame;
        const resize = () => {
            const { width, height } = host.getBoundingClientRect();
            const safeWidth = Math.max(width, 1);
            const safeHeight = Math.max(height, 1);
            camera.aspect = safeWidth / safeHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(safeWidth, safeHeight, false);
        };
        const resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(host);
        resize();

        const loader = new GLTFLoader();
        loader.load(HUMAN_MODEL_URL, (gltf) => {
            if (disposed) return;
            const model = gltf.scene;
            modelRef.current = model;
            scene.add(model);
            model.updateMatrixWorld(true);
            const bounds = new THREE.Box3().setFromObject(model);
            const center = bounds.getCenter(new THREE.Vector3());
            const size = bounds.getSize(new THREE.Vector3());
            const radius = Math.max(size.x, size.y, size.z) * 0.5;
            controls.target.copy(center);
            camera.position.set(center.x + radius * 1.4, center.y + radius * 0.7, center.z + radius * 2.5);
            camera.near = Math.max(radius / 1000, 0.001);
            camera.far = Math.max(radius * 20, 100);
            camera.updateProjectionMatrix();
            controls.update();

            let markerCount = 0;
            const standardPoints = {};
            const highlightedNames = new Set(standardNodeNames);
            model.traverse((object) => {
                if (!isMarkerNode(object)) return;
                const marker = new THREE.Mesh(
                    new THREE.SphereGeometry(Math.max(radius * 0.012, 0.012), 12, 8),
                    new THREE.MeshStandardMaterial({ color: '#f7c948', emissive: '#7d5a00', emissiveIntensity: 1.2 }),
                );
                marker.position.copy(object.getWorldPosition(new THREE.Vector3()));
                marker.userData.nodeName = object.name;
                marker.userData.pointName = pointNameMapRef.current[object.name] || object.name;
                marker.userData.isCasePoint = highlightedNames.has(object.name);
                if (marker.userData.isCasePoint) standardMarkerGroup.add(marker);
                else markerGroup.add(marker);
                if (marker.userData.isCasePoint) {
                    standardPoints[object.name] = marker.position.toArray();
                }
                markerCount += 1;
            });
            onStandardPointsReady?.(standardPoints);
            setModelStats({ markers: markerCount });
            setStatus(`模型加载完成 · 穴位 ${markerCount}`);
        }, undefined, () => {
            if (!disposed) setStatus('模型加载失败，请检查 GLB 文件和前端服务');
        });

        const render = () => {
            if (disposed) return;
            controls.update();
            renderer.render(scene, camera);
            animationFrame = requestAnimationFrame(render);
        };
        animationFrame = requestAnimationFrame(render);

        return () => {
            disposed = true;
            cancelAnimationFrame(animationFrame);
            resizeObserver.disconnect();
            controls.dispose();
            renderer.dispose();
            markerGroup.clear();
            standardMarkerGroup.clear();
            disposePlacedNeedles(needleGroup);
            scene.clear();
            delete host.__acupunctureCamera;
            host.replaceChildren();
        };
    }, [onStandardPointsReady, standardNodeNames]);

    useEffect(() => {
        const names = new Set(highlightNames);
        [...(markerGroupRef.current?.children || []), ...(standardMarkerGroupRef.current?.children || [])].forEach((marker) => {
            const highlighted = names.has(marker.userData.nodeName)
                || (revealStandardPoints && marker.userData.isCasePoint);
            const hovered = marker.uuid === hoveredPoint?.markerId;
            marker.material.color.set(hovered ? '#3b82f6' : highlighted ? '#f97316' : '#f7c948');
            marker.material.emissive.set(hovered ? '#123a7a' : highlighted ? '#9a3412' : '#7d5a00');
            marker.scale.setScalar(hovered ? 1.9 : highlighted ? 1.7 : 1);
        });
    }, [highlightNames, hoveredPoint?.markerId, modelStats.markers, revealStandardPoints, standardNodeNames]);

    useEffect(() => {
        const group = needleGroupRef.current;
        if (!group) return;
        disposePlacedNeedles(group);
        needles.forEach((needle, index) => {
            if (!Array.isArray(needle.point) || needle.point.length !== 3) return;
            group.add(createRealisticNeedle(needle, index));
        });
    }, [needles, needleTextureVersion]);

    const handlePointerDown = (event) => {
        if (!interactive || !modelRef.current || !onSurfacePick) return;
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture?.(event.pointerId);
        const rect = event.currentTarget.getBoundingClientRect();
        pointerRef.current.set(
            ((event.clientX - rect.left) / rect.width) * 2 - 1,
            -((event.clientY - rect.top) / rect.height) * 2 + 1,
        );
        const camera = event.currentTarget.__acupunctureCamera;
        if (!camera) return;
        raycasterRef.current.setFromCamera(pointerRef.current, camera);
        const hit = raycasterRef.current.intersectObject(modelRef.current, true).find((item) => item.object.isMesh);
        if (!hit) {
            pendingPickRef.current = null;
            pointerGestureRef.current = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                moved: false,
            };
            return;
        }
        const normal = hit.face?.normal?.clone();
        if (normal) {
            normal.applyMatrix3(
                new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld),
            ).normalize();
        }
        pendingPickRef.current = { point: hit.point.toArray(), normal: normal?.toArray() || null };
        pointerGestureRef.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            moved: false,
        };
    };

    const handlePointerMove = (event) => {
        if (showPointNames) {
            const rect = event.currentTarget.getBoundingClientRect();
            pointerRef.current.set(
                ((event.clientX - rect.left) / rect.width) * 2 - 1,
                -((event.clientY - rect.top) / rect.height) * 2 + 1,
            );
            const camera = event.currentTarget.__acupunctureCamera;
            if (camera) {
                raycasterRef.current.setFromCamera(pointerRef.current, camera);
                const visibleMarkers = [
                    ...(markerGroupRef.current?.visible ? markerGroupRef.current.children : []),
                    ...(standardMarkerGroupRef.current?.visible ? standardMarkerGroupRef.current.children : []),
                ];
                const markerHit = raycasterRef.current.intersectObjects(visibleMarkers, false)[0];
                setHoveredPoint(markerHit ? {
                    markerId: markerHit.object.uuid,
                    name: markerHit.object.userData.pointName,
                    x: event.clientX - rect.left,
                    y: event.clientY - rect.top,
                } : null);
            }
        } else if (hoveredPoint) {
            setHoveredPoint(null);
        }
        const gesture = pointerGestureRef.current;
        if (!gesture || gesture.pointerId !== event.pointerId) return;
        const distance = Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY);
        if (distance > NEEDLE_TAP_THRESHOLD_PX) {
            gesture.moved = true;
            pendingPickRef.current = null;
        }
    };

    const handlePointerUp = (event) => {
        const gesture = pointerGestureRef.current;
        if (!gesture || gesture.pointerId !== event.pointerId) return;
        const shouldPlaceNeedle = !gesture.moved && pendingPickRef.current;
        event.currentTarget.releasePointerCapture?.(event.pointerId);
        if (shouldPlaceNeedle) onSurfacePick(pendingPickRef.current);
        pendingPickRef.current = null;
        pointerGestureRef.current = null;
    };

    return (
        <div className={`acupuncture-model${interactive ? ' is-interactive' : ''}${expanded ? ' is-expanded' : ''}${fullScreen ? ' is-fullscreen' : ''}`}>
            <div ref={(node) => {
                hostRef.current = node;
            }} className="acupuncture-model__viewport" onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp} />
            <button type="button" className="acupuncture-model__markers-button" onClick={onToggleMarkers}>
                {revealStandardPoints
                    ? (showMarkers ? '隐藏其他穴位' : '显示其他穴位')
                    : (showMarkers ? '隐藏穴位' : '显示穴位')}
            </button>
            <button type="button" className="acupuncture-model__names-button" onClick={() => setShowPointNames((value) => !value)}>
                {showPointNames ? '隐藏穴位名称' : '显示穴位名称'}
            </button>
            {showPointNames && hoveredPoint && <span
                className="acupuncture-model__point-tooltip"
                style={{ left: hoveredPoint.x, top: hoveredPoint.y }}
            >{hoveredPoint.name}</span>}
            <div className="acupuncture-model__status">{status}</div>
            <span className="acupuncture-model__hint">拖拽旋转 · 滚轮缩放{interactive ? ' · 点击并松开人体记录针位' : ''}</span>
            <span className="acupuncture-model__count">穴位 {modelStats.markers}</span>
        </div>
    );
}
