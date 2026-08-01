# Realistic Acupuncture Needle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the green placement cylinder with a reference-faithful silver 3D acupuncture needle that stays clear in the training scene.

**Architecture:** Keep `needles` and the existing point, normal, and angle semantics unchanged. Generate and track `frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb`, then preload and clone that asset in `AcupunctureModelCanvas.jsx`; a deterministic helper calculates direction and a small wrapper adds the placement glow. Make the renderer transparent so the CSS near-black teal backdrop is visible behind the model.

**Tech Stack:** React 19, Three.js r0.185, Vitest 4, Vite.

---

### Task 1: Cover the needle assembly with a focused unit test

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx`
- Create: `frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb`
- Create: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`

**Step 1: Write the failing test**

Import named exports `getNeedleDirection` and `createRealisticNeedle` from the canvas module; use Three.js objects directly instead of mounting WebGL.

```jsx
it('builds a silver needle with all reference features', () => {
    const needle = createRealisticNeedle({
        id: 'needle-1', point: [1, 2, 3], normal: [0, 1, 0], insertionType: 'direct',
    });
    expect(needle.userData.needleId).toBe('needle-1');
    expect(needle.children.find((child) => child.name === 'needle-shaft')).toBeTruthy();
    expect(needle.children.find((child) => child.name === 'needle-tip')).toBeTruthy();
    expect(needle.children.find((child) => child.name === 'needle-handle')).toBeTruthy();
    expect(needle.children.find((child) => child.name === 'needle-loop')).toBeTruthy();
    expect(needle.children.find((child) => child.name === 'needle-contact-glow')).toBeTruthy();
    expect(needle.children.filter((child) => child.name === 'needle-grip-ring')).toHaveLength(10);
});

it('keeps direct, oblique and transverse directions distinct', () => {
    const normal = new THREE.Vector3(0, 1, 0);
    expect(getNeedleDirection(normal, 0, 0).angleTo(normal)).toBeCloseTo(0);
    expect(getNeedleDirection(normal, 45, 0).angleTo(normal)).toBeCloseTo(Math.PI / 4);
    expect(getNeedleDirection(normal, 75, 0).angleTo(normal)).toBeCloseTo(THREE.MathUtils.degToRad(75));
});
```

**Step 2: Run the test to verify it fails**

Run: `npm --prefix frontend/llm run test:unit -- AcupunctureModelCanvas.test.jsx`

Expected: FAIL because the two named exports do not exist.

### Task 2: Implement the reference-faithful needle

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx:20-25, 110-125, 205-238`
- Test: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`

**Step 1: Add `getNeedleDirection`**

Extract the current reference/tangent/bitangent calculation into the named helper. Its inputs are the surface normal, tilt angle, and direction angle; it returns the normalized outward insertion direction. This prevents a visual refactor from changing direct, oblique, or transverse behaviour.

**Step 2: Generate and add the tracked needle asset**

Create `frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb` from one local-Y `THREE.Group`. Use one silver `MeshPhysicalMaterial` (`#d9dee2`, metalness `0.96`, roughness `0.2`, small clearcoat) for:

- a thin cylinder named `needle-shaft`;
- a matching conical `needle-tip` below it;
- a larger cylinder named `needle-handle`;
- exactly ten shallow torus rings named `needle-grip-ring` over the handle;
- a torus eye named `needle-loop` above the grip.

The GLB contains the shaft, tip, grip, grip rings and loop only. Do not add the contact glow to the binary asset: the canvas creates it dynamically at the skin contact point. Verify the new `.glb` is tracked by Git and does not reference the desktop images.

**Step 3: Preload and clone the needle asset**

Load `/acupuncture-models/realistic-acupuncture-needle.glb` in parallel with the body GLB and cache its scene as `needleTemplateRef`. `createRealisticNeedle` clones that template, creates the named `needle-contact-glow`, places it at `needle.point`, rotates it from local Y to `getNeedleDirection(...)`, and stores `needle.id || `needle-${index + 1}`` in `userData.needleId`.

**Step 4: Replace the green cylinder**

Retain `group.clear()` and the malformed-point guard in the current `needles` effect. Replace the `CylinderGeometry`/green `MeshStandardMaterial` block with `group.add(createRealisticNeedle(needle, index))`; do not touch `AcupuncturePractice.jsx`, scoring, placement payloads, or undo logic.

**Step 5: Improve metal lighting**

Keep the white key light, reduce the saturated-green fill, and add a warm rim directional light from the camera-rear side. The result should form a bright edge on silver metal without changing its material color.

**Step 6: Run the focused test**

Run: `npm --prefix frontend/llm run test:unit -- AcupunctureModelCanvas.test.jsx`

Expected: PASS.

**Step 7: Commit**

```powershell
git add -- frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx
git commit -m "feat: render realistic acupuncture needles"
```

### Task 3: Add the contrast-preserving scene backdrop

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx:99-117`
- Modify: `frontend/llm/src/components/acupuncture/acupuncturePractice.css:458-468`
- Test: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`

**Step 1: Make the canvas transparent**

Construct `WebGLRenderer` with `alpha: true`, set a transparent clear color, and leave `scene.background` unset. Preserve antialiasing, color-space, pixel ratio and the existing cleanup lifecycle.

**Step 2: Add the CSS backdrop**

Keep the viewport dimensions and controls unchanged. Give `.acupuncture-model__viewport` this layered background, retaining `overflow: hidden`; keep its canvas a block element.

```css
.acupuncture-model__viewport {
    overflow: hidden;
    background:
        radial-gradient(circle at 72% 24%, rgba(255, 226, 177, .16), transparent 31%),
        radial-gradient(circle at 24% 84%, rgba(31, 105, 96, .18), transparent 38%),
        linear-gradient(135deg, #071512 0%, #0c211e 48%, #050a0a 100%);
}
```

**Step 3: Run regression tests**

Run: `npm --prefix frontend/llm run test:unit -- AcupunctureModelCanvas.test.jsx`

Run: `npm --prefix frontend/llm run test:component -- AcupuncturePractice.test.jsx`

Expected: PASS. Existing mocked component tests must retain the placement, undo, review, and fullscreen exit contract.

**Step 4: Build**

Run: `npm --prefix frontend/llm run build`

Expected: Vite build succeeds without an import or bundling error.

**Step 5: Browser visual acceptance**

Start the project with its documented frontend command. At desktop and narrow/mobile widths, verify:

1. direct needles show a reflective silver shaft, threaded grip and ring;
2. oblique and transverse needles visibly differ in orientation;
3. silver needles remain visible over both light and dark model surfaces;
4. the amber contact glow identifies placement without hiding skin or acupuncture markers;
5. model rotation, placement, undo and correct-answer review still work.

**Step 6: Commit**

```powershell
git add -- frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx frontend/llm/src/components/acupuncture/acupuncturePractice.css frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx
git commit -m "style: improve acupuncture needle contrast"
```
