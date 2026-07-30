import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

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

export default function AcupunctureModelCanvas({
    showMarkers = false,
    highlightNames = [],
    standardNodeNames = [],
    revealStandardPoints = false,
    expanded = false,
    fullScreen = false,
    onStandardPointsReady,
    interactive = false,
    onSurfacePick,
    onToggleMarkers,
    needles = [],
}) {
    const hostRef = useRef(null);
    const sceneRef = useRef(null);
    const markerGroupRef = useRef(null);
    const standardMarkerGroupRef = useRef(null);
    const needleGroupRef = useRef(null);
    const modelRef = useRef(null);
    const raycasterRef = useRef(new THREE.Raycaster());
    const pointerRef = useRef(new THREE.Vector2());
    const [status, setStatus] = useState('正在加载 3D 模型…');
    const [modelStats, setModelStats] = useState({ markers: 0 });

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return undefined;
        const scene = new THREE.Scene();
        scene.background = new THREE.Color('#333333');
        sceneRef.current = scene;

        const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 1000);
        host.__acupunctureCamera = camera;
        let renderer;
        try {
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
        } catch {
            queueMicrotask(() => setStatus('当前环境不支持 WebGL，请使用支持 3D 加速的浏览器'));
            return () => {
                delete host.__acupunctureCamera;
                scene.clear();
            };
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        host.replaceChildren(renderer.domElement);

        scene.add(new THREE.HemisphereLight('#d8fff2', '#193c35', 2.4));
        const keyLight = new THREE.DirectionalLight('#ffffff', 3.2);
        keyLight.position.set(4, 7, 5);
        scene.add(keyLight);
        const fillLight = new THREE.DirectionalLight('#6ee7c2', 1.5);
        fillLight.position.set(-5, 2, -4);
        scene.add(fillLight);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.minDistance = 0.05;
        controls.maxDistance = 100;

        const markerGroup = new THREE.Group();
        markerGroup.visible = false;
        markerGroupRef.current = markerGroup;
        scene.add(markerGroup);
        const standardMarkerGroup = new THREE.Group();
        standardMarkerGroup.visible = false;
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
        loader.load('/acupuncture-models/blender.yibiaozhu.glb', (gltf) => {
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
            needleGroup.clear();
            scene.clear();
            delete host.__acupunctureCamera;
            host.replaceChildren();
        };
    }, [onStandardPointsReady, standardNodeNames]);

    useEffect(() => {
        if (markerGroupRef.current) markerGroupRef.current.visible = showMarkers;
        if (standardMarkerGroupRef.current) standardMarkerGroupRef.current.visible = revealStandardPoints;
    }, [revealStandardPoints, showMarkers]);

    useEffect(() => {
        const names = new Set(highlightNames);
        [...(markerGroupRef.current?.children || []), ...(standardMarkerGroupRef.current?.children || [])].forEach((marker) => {
            const highlighted = names.has(marker.userData.nodeName)
                || (revealStandardPoints && marker.userData.isCasePoint);
            marker.material.color.set(highlighted ? '#ff5f57' : '#f7c948');
            marker.scale.setScalar(highlighted ? 1.7 : 1);
        });
    }, [highlightNames, modelStats.markers, revealStandardPoints, standardNodeNames]);

    useEffect(() => {
        const group = needleGroupRef.current;
        if (!group) return;
        group.clear();
        needles.forEach((needle, index) => {
            if (!Array.isArray(needle.point) || needle.point.length !== 3) return;
            const point = new THREE.Vector3(...needle.point);
            const normal = new THREE.Vector3(...(needle.normal || [0, 1, 0])).normalize();
            const reference = Math.abs(normal.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0);
            const tangent = new THREE.Vector3().crossVectors(reference, normal).normalize();
            const bitangent = new THREE.Vector3().crossVectors(normal, tangent).normalize();
            const insertionTilts = { direct: 0, oblique: 45, transverse: 78 };
            const tilt = THREE.MathUtils.degToRad(insertionTilts[needle.insertionType] ?? Number(needle.tiltAngle || 0));
            const azimuth = THREE.MathUtils.degToRad(Number(needle.directionAngle || 0));
            const direction = normal.clone().multiplyScalar(Math.cos(tilt))
                .add(tangent.multiplyScalar(Math.sin(tilt) * Math.cos(azimuth)))
                .add(bitangent.multiplyScalar(Math.sin(tilt) * Math.sin(azimuth)))
                .normalize();
            const length = 0.22;
            const shaft = new THREE.Mesh(
                new THREE.CylinderGeometry(0.012, 0.012, length, 10),
                new THREE.MeshStandardMaterial({ color: '#22c55e', emissive: '#166534', emissiveIntensity: 0.65 }),
            );
            shaft.position.copy(point).addScaledVector(direction, length * 0.5);
            shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
            shaft.userData.needleId = needle.id || `needle-${index + 1}`;
            group.add(shaft);
        });
    }, [needles]);

    const handlePointerDown = (event) => {
        if (!interactive || !modelRef.current || !onSurfacePick) return;
        const rect = event.currentTarget.getBoundingClientRect();
        pointerRef.current.set(
            ((event.clientX - rect.left) / rect.width) * 2 - 1,
            -((event.clientY - rect.top) / rect.height) * 2 + 1,
        );
        const camera = event.currentTarget.__acupunctureCamera;
        if (!camera) return;
        raycasterRef.current.setFromCamera(pointerRef.current, camera);
        const hit = raycasterRef.current.intersectObject(modelRef.current, true).find((item) => item.object.isMesh);
        if (hit) onSurfacePick({ point: hit.point.toArray(), normal: hit.face?.normal?.toArray() || null });
    };

    return (
        <div className={`acupuncture-model${interactive ? ' is-interactive' : ''}${expanded ? ' is-expanded' : ''}${fullScreen ? ' is-fullscreen' : ''}`}>
            <div ref={(node) => {
                hostRef.current = node;
            }} className="acupuncture-model__viewport" onPointerDown={handlePointerDown} />
            <button type="button" className="acupuncture-model__markers-button" onClick={onToggleMarkers}>
                {revealStandardPoints ? (showMarkers ? '隐藏其他穴位' : '显示其他穴位') : (showMarkers ? '隐藏穴位' : '显示穴位')}
            </button>
            <div className="acupuncture-model__status">{status}</div>
            <span className="acupuncture-model__hint">拖拽旋转 · 滚轮缩放{interactive ? ' · 点击人体记录针位' : ''}</span>
            <span className="acupuncture-model__count">穴位 {modelStats.markers}</span>
        </div>
    );
}
