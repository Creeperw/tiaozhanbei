# Acupuncture Image Needle Assets Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the runtime GLB needle appearance with transparent versions of the user-supplied needle images, using the frontal asset for direct insertion and the oblique asset for oblique/transverse insertion.

**Architecture:** Store two processed transparent PNG files under `frontend/llm/public/acupuncture/needles/`. Keep the existing placement direction calculation, but render each placed needle as a `THREE.Sprite` or camera-facing textured plane that chooses its image from `needle.insertionType`; the image itself supplies the realistic visual detail. Keep the existing amber contact glow and scene backdrop. Remove the superseded runtime GLB only after code no longer requests it.

**Tech Stack:** React 19, Three.js r0.185, Vitest 4, Vite, image processing.

---

### Task 1: Prepare tracked transparent needle assets

**Files:**

- Create: `frontend/llm/public/acupuncture/needles/needle-direct.png`
- Create: `frontend/llm/public/acupuncture/needles/needle-oblique.png`
- Delete: `frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb`

**Step 1: Process the source images**

Use the user-provided `D:/HuaweiMoveData/Users/11075/Desktop/针1.png` and `针2.png` only as local source inputs. Remove their dark gray backgrounds while retaining the full needle, warm metal highlights, ring handle, and fine tip. Do not alter the needle shape or add text.

**Step 2: Save production assets**

Save the transparent outputs as `needle-direct.png` (from `针1.png`) and `needle-oblique.png` (from `针2.png`). Verify both have alpha transparency and reasonable cropped bounds; keep them in the project so Git tracks them. Do not add desktop source images to the repository.

**Step 3: Verify the asset contract**

Use a small image-inspection command to confirm both destination files exist, have an alpha channel, and are nonempty. Confirm no runtime source still requires the old GLB before deleting that exact, tracked generated asset.

### Task 2: Test the image-selection and placement contract

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`
- Modify: `frontend/llm/src/components/acupuncture/realisticNeedle.js`

**Step 1: Write failing tests**

Replace the GLB-structure assertions with real helper assertions for the new texture choice and placement contract.

```jsx
it('uses the frontal needle asset for direct insertion', () => {
    expect(getNeedleImageUrl({ insertionType: 'direct' }))
        .toBe('/acupuncture/needles/needle-direct.png');
});

it.each(['oblique', 'transverse'])('uses the oblique needle asset for %s insertion', (insertionType) => {
    expect(getNeedleImageUrl({ insertionType }))
        .toBe('/acupuncture/needles/needle-oblique.png');
});

it('keeps the existing needle direction for image placement', () => {
    expect(getNeedleDirection(new THREE.Vector3(0, 1, 0), 45, 0).angleTo(new THREE.Vector3(0, 1, 0)))
        .toBeCloseTo(Math.PI / 4);
});
```

**Step 2: Run to verify red**

Run: `npm --prefix frontend/llm run test:unit -- AcupunctureModelCanvas.test.jsx`

Expected: FAIL because `getNeedleImageUrl` is not yet implemented.

### Task 3: Render the processed images as needles

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/realisticNeedle.js`
- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx`
- Test: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`

**Step 1: Add deterministic asset selection**

Export `getNeedleImageUrl(needle)`. Return the direct asset only for `direct`; use the oblique asset for `oblique`, `transverse`, and missing/unknown values so the appearance stays intentional.

**Step 2: Load and cache textures**

Use a single `THREE.TextureLoader` and module-level cache keyed by the two public asset URLs. Configure alpha-aware color space and clamp wrapping. If a texture is not ready, do not fall back to a green line or the old GLB; delay the visual instance until the texture arrives, then refresh the current needle group.

**Step 3: Create camera-facing visual instances**

Replace the GLB template loader and clone path with a `THREE.Sprite` using `SpriteMaterial({ map, transparent: true, depthWrite: false })`. Size the sprite from the processed image aspect ratio and anchor its lower tip at the selected skin point. Keep `needle.userData.needleId`, contact glow, and the existing direction vector data. For non-direct insertion, rotate/offset the image presentation consistently with the direction vector without changing placement or scoring data.

**Step 4: Remove old GLB runtime support**

Delete the GLB request, template cache, fallback GLB assembly code, and superseded GLB file. Retain only the shared contact-glow resources and their correct disposal lifecycle.

**Step 5: Run focused tests**

Run: `npm --prefix frontend/llm run test:unit -- AcupunctureModelCanvas.test.jsx`

Expected: PASS.

### Task 4: Verify build and visible behavior

**Files:**

- Modify only if a verification failure requires it.

**Step 1: Run targeted lint**

Run: `npm --prefix frontend/llm exec eslint src/components/acupuncture/AcupunctureModelCanvas.jsx src/components/acupuncture/realisticNeedle.js src/components/acupuncture/AcupunctureModelCanvas.test.jsx`

Expected: PASS.

**Step 2: Build**

Run: `npm --prefix frontend/llm run build`

Expected: PASS.

**Step 3: Visual verification**

At desktop and narrow/mobile viewport widths, place direct, oblique, and transverse needles. Verify: no rectangular image background is visible; direct needles use the frontal source image; oblique and transverse needles use the angled source image; the needle tip stays at the selected point; the dark teal background and amber contact glow remain readable; undo and review still work.

**Step 4: Commit**

```powershell
git add -- frontend/llm/public/acupuncture/needles/needle-direct.png frontend/llm/public/acupuncture/needles/needle-oblique.png frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.jsx frontend/llm/src/components/acupuncture/realisticNeedle.js frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx
git commit -m "feat: use supplied acupuncture needle imagery"
```
