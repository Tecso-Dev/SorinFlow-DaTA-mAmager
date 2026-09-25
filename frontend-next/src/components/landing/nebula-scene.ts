// The landing page's holographic particle nebula, ported from the old
// frontend/landing.html to TypeScript and three.js from npm. ~14k points
// (fewer on phones) drawn by one custom shader with additive light; as the
// visitor scrolls they flow from one formation to the next:
//   hero: nebula → features: isometric city → how/AI: the 3D house →
//   security: a key → contact: infinity.
// This module is only ever loaded with a dynamic import when the browser is
// idle (see nebula.tsx), so three.js never competes with the headline.

import {
  AdditiveBlending, BufferAttribute, BufferGeometry, Color, NormalBlending, PerspectiveCamera, Points, Scene,
  ShaderMaterial, Timer, WebGLRenderer,
} from "three";

export type Formation = "nebula" | "city" | "house" | "key" | "infinity";

export type NebulaOptions = {
  /** Section elements in page order and the formation each one shows. */
  anchors: { el: HTMLElement; formation: Formation }[];
  dark: boolean;
};

export type NebulaHandle = {
  setActive(active: boolean): void;
  setDark(dark: boolean): void;
  dispose(): void;
};

type Rand = () => number;

/** A small seeded generator, so every visit draws the same shapes. */
function mulberry32(seed: number): Rand {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ───────────────────────── formations (each N×3 floats) ───────────────────────── */

function nebula(N: number, rnd: Rand, cx: number, cy: number, spread: number) {
  const p = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const cluster = Math.floor(rnd() * 5);
    const ox = Math.sin(cluster * 2.4) * spread * 0.55;
    const oy = Math.cos(cluster * 1.7) * spread * 0.3;
    const r = Math.pow(rnd(), 2.2) * spread;
    const th = rnd() * Math.PI * 2, ph = Math.acos(rnd() * 2 - 1);
    p[i * 3] = cx + ox + r * Math.sin(ph) * Math.cos(th);
    p[i * 3 + 1] = cy + oy + r * Math.sin(ph) * Math.sin(th);
    p[i * 3 + 2] = r * Math.cos(ph) * 0.6 - 1;
  }
  return p;
}

type V3 = [number, number, number];

/** A random point on one of the segments, longer segments drawing more points. */
function onEdges(segs: [V3, V3][], rnd: Rand): V3 {
  const lens = segs.map(([a, b]) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
  let r = rnd() * lens.reduce((x, y) => x + y, 0);
  let i = 0;
  while (r > lens[i] && i < segs.length - 1) r -= lens[i++];
  const [a, b] = segs[i], t = rnd(), j = () => (rnd() - 0.5) * 0.08;
  return [a[0] + (b[0] - a[0]) * t + j(), a[1] + (b[1] - a[1]) * t + j(), a[2] + (b[2] - a[2]) * t + j()];
}

/** Turn a point toward the viewer by `yaw` around y, then tip it toward us. */
function isoView(x: number, y: number, z: number, yaw = Math.PI / 4, tilt = 0.38): [number, number, number] {
  const c = Math.cos(yaw), s = Math.sin(yaw);
  const rx = x * c - z * s, rz = x * s + z * c;
  const ct = Math.cos(tilt), st = Math.sin(tilt);
  return [rx, y * ct - rz * st, y * st + rz * ct];
}

/** A small city of isometric towers; the particles trace their edges, a few
 *  lit windows and the street grid. */
function city(N: number, rnd: Rand) {
  const p = new Float32Array(N * 3);
  const edges: [V3, V3][] = [];
  const faces: { x: number; z: number; w: number; h: number }[] = [];
  for (let gx = -3; gx <= 3; gx++) {
    for (const gz of [-0.55, 0.75]) {
      if (rnd() < 0.1) continue;
      const centre = 1 - Math.abs(gx) / 4.2;
      const h = 0.8 + rnd() * 1.2 + centre * 3.2 * (0.6 + rnd() * 0.4);
      const w = 0.42 + rnd() * 0.12, x = gx * 1.5 + (gz > 0 ? 0.55 : 0), z = gz * 1.45;
      const c: V3[] = [[x - w, 0, z - w], [x + w, 0, z - w], [x + w, 0, z + w], [x - w, 0, z + w]];
      for (let k = 0; k < 4; k++) {
        const a = c[k], b = c[(k + 1) % 4];
        edges.push([a, [a[0], h, a[2]]]); // vertical edge
        edges.push([[a[0], h, a[2]], [b[0], h, b[2]]]); // roof outline
      }
      faces.push({ x, z: z + w, w, h });
    }
  }
  for (let i = 0; i < N; i++) {
    const part = rnd();
    let q: V3;
    if (part < 0.72) {
      q = onEdges(edges, rnd);
    } else if (part < 0.9) {
      // lit windows, in storeys, on the faces toward the viewer
      const f = faces[Math.floor(rnd() * faces.length)];
      const storeys = Math.max(1, Math.floor(f.h / 0.45));
      q = [f.x + (rnd() - 0.5) * f.w * 1.6, (Math.floor(rnd() * storeys) + 0.5) * (f.h / storeys), f.z];
    } else {
      // the street grid
      const along = rnd() < 0.5;
      const lane = (Math.floor(rnd() * 8) - 3.5) * 1.45;
      q = along ? [(rnd() - 0.5) * 11, 0, Math.max(-2.9, Math.min(2.9, lane / 2))] : [lane, 0, (rnd() - 0.5) * 5.8];
    }
    const [px, py, pz] = isoView(q[0], q[1] - 2, q[2], 0.42, 0.3);
    p[i * 3] = px * 1.1;
    p[i * 3 + 1] = py * 1.1;
    p[i * 3 + 2] = pz - 1;
  }
  return p;
}

/** The old landing's house, now drawn mostly along its edges so it reads as
 *  a house and not a cloud: walls, a gabled roof, a door, a chimney, a halo. */
function house(N: number, rnd: Rand) {
  const p = new Float32Array(N * 3);
  const W = 4.6, D = 3.2, H = 2.6, RH = 1.9, y0 = -2.2, x = W / 2, z = D / 2, top = y0 + H, peak = top + RH;
  const edges: [V3, V3][] = [
    // floor and eaves
    [[-x, y0, -z], [x, y0, -z]], [[-x, y0, z], [x, y0, z]], [[-x, y0, -z], [-x, y0, z]], [[x, y0, -z], [x, y0, z]],
    [[-x, top, -z], [x, top, -z]], [[-x, top, z], [x, top, z]], [[-x, top, -z], [-x, top, z]], [[x, top, -z], [x, top, z]],
    // corners
    [[-x, y0, -z], [-x, top, -z]], [[x, y0, -z], [x, top, -z]], [[-x, y0, z], [-x, top, z]], [[x, y0, z], [x, top, z]],
    // gables and ridge
    [[-x, top, z], [0, peak, z]], [[x, top, z], [0, peak, z]], [[-x, top, -z], [0, peak, -z]], [[x, top, -z], [0, peak, -z]],
    [[0, peak, -z], [0, peak, z]],
    // door and window frames on the front
    [[-0.45, y0, z], [-0.45, y0 + 1.3, z]], [[0.45, y0, z], [0.45, y0 + 1.3, z]], [[-0.45, y0 + 1.3, z], [0.45, y0 + 1.3, z]],
    [[1.1, y0 + 1.1, z], [1.8, y0 + 1.1, z]], [[1.1, y0 + 1.8, z], [1.8, y0 + 1.8, z]],
    [[1.1, y0 + 1.1, z], [1.1, y0 + 1.8, z]], [[1.8, y0 + 1.1, z], [1.8, y0 + 1.8, z]],
    // chimney
    [[-1.4, top + 0.9, -0.3], [-1.4, top + 1.9, -0.3]], [[-0.9, top + 0.6, -0.3], [-0.9, top + 1.9, -0.3]],
    [[-1.4, top + 1.9, -0.3], [-0.9, top + 1.9, -0.3]],
  ];
  for (let i = 0; i < N; i++) {
    const u = rnd(), v = rnd(), w = rnd(), part = rnd();
    let q: V3;
    if (part < 0.55) {
      q = onEdges(edges, rnd);
    } else if (part < 0.72) {
      // a thin haze on the roof planes
      const s = w < 0.5 ? 1 : -1;
      q = [s * (u * W) / 2, top + RH * (1 - u), (v - 0.5) * D];
    } else if (part < 0.86) {
      // a thin haze on the walls
      q = w < 0.5 ? [(u - 0.5) * W, y0 + v * H, w < 0.25 ? z : -z] : [w < 0.75 ? x : -x, y0 + v * H, (u - 0.5) * D];
    } else {
      // the ground halo
      const th = u * Math.PI * 2, r = 3.4 + v * 1.4;
      q = [Math.cos(th) * r, y0 - 0.15, Math.sin(th) * r * 0.8];
    }
    // turned toward the isometric view, so two walls show
    const c = Math.cos(0.6), sn = Math.sin(0.6);
    p[i * 3] = q[0] * c - q[2] * sn;
    p[i * 3 + 1] = q[1] + 0.6;
    p[i * 3 + 2] = q[0] * sn + q[2] * c - 0.8;
  }
  return p;
}

/** A key: a ring bow, a shaft and three teeth, lying slightly tilted. */
function key(N: number, rnd: Rand) {
  const p = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const part = rnd(), u = rnd(), v = rnd();
    const j = () => (rnd() - 0.5) * 0.22;
    let x: number, y: number, z: number;
    if (part < 0.45) {
      // the bow: a thick torus
      const th = u * Math.PI * 2, ph = v * Math.PI * 2, R = 1.6, r = 0.38;
      x = -3.2 + (R + r * Math.cos(ph)) * Math.cos(th);
      y = (R + r * Math.cos(ph)) * Math.sin(th);
      z = r * Math.sin(ph);
    } else if (part < 0.8) {
      // the shaft, a cylinder
      const th = v * Math.PI * 2;
      x = -1.4 + u * 5.6; y = Math.cos(th) * 0.26; z = Math.sin(th) * 0.26;
    } else {
      // three teeth of different lengths
      const t = Math.floor(v * 3), len = [1.1, 0.7, 0.95][t];
      x = 2.4 + t * 0.65 + j(); y = -0.26 - u * len; z = j();
    }
    const c = Math.cos(-0.35), s = Math.sin(-0.35);
    p[i * 3] = x * c - y * s * 0.2 + j() * 0.3;
    p[i * 3 + 1] = x * s * 0.35 + y * c;
    p[i * 3 + 2] = z - 1 + x * 0.12;
  }
  return p;
}

function infinity(N: number, rnd: Rand) {
  const p = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const t = rnd() * Math.PI * 2;
    const s = 4.6 / (1 + Math.sin(t) ** 2);
    const j = () => (rnd() - 0.5) * 0.6 * Math.pow(rnd(), 1.4);
    p[i * 3] = s * Math.cos(t) + j();
    p[i * 3 + 1] = s * Math.sin(t) * Math.cos(t) * 0.85 + j();
    p[i * 3 + 2] = j() * 2 - 1;
  }
  return p;
}

/* ───────────────────────── the scene ───────────────────────── */

// «شب نیلی»: indigo, violet and cyan, lighter for additive light on black,
// deeper for ordinary blending on the light page.
const DARK_PALETTE = ["#a5b4fc", "#818cf8", "#c4b5fd", "#a78bfa", "#67e8f9", "#22d3ee", "#e0e7ff"];
const LIGHT_PALETTE = ["#4f46e5", "#4338ca", "#7c3aed", "#6d28d9", "#0891b2", "#0e7490", "#6366f1"];

const VERTEX = /* glsl */ `
  attribute float aSize; attribute float aSeed; attribute vec3 aColor;
  uniform float uTime; uniform float uPx;
  varying vec3 vC; varying float vTw;
  void main() {
    vC = aColor;
    vec3 p = position;
    p.x += sin(uTime * .5 + aSeed) * .08;
    p.y += cos(uTime * .4 + aSeed * 1.7) * .08;
    p.z += sin(uTime * .3 + aSeed * 2.3) * .06;
    vTw = .55 + .45 * sin(uTime * 1.6 + aSeed * 3.0);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = aSize * uPx * 40.0 / -mv.z;
    gl_Position = projectionMatrix * mv;
  }`;

const FRAGMENT = /* glsl */ `
  uniform float uAlpha;
  varying vec3 vC; varying float vTw;
  void main() {
    float d = length(gl_PointCoord - .5);
    float a = smoothstep(.5, .04, d);
    gl_FragColor = vec4(vC, a * uAlpha * vTw);
  }`;

export function startNebula(canvas: HTMLCanvasElement, opts: NebulaOptions): NebulaHandle {
  const renderer = new WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: "low-power" });
  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  renderer.setPixelRatio(dpr);

  const phone = Math.min(window.innerWidth, window.innerHeight) < 640 || (navigator.hardwareConcurrency ?? 8) <= 4;
  const N = phone ? 6000 : 14000;
  const rnd = mulberry32(1405);

  const scene = new Scene();
  const camera = new PerspectiveCamera(50, 1, 0.1, 100);
  camera.position.z = 11;

  const builders: Record<Formation, () => Float32Array> = {
    nebula: () => nebula(N, rnd, -4.5, 2.2, 4.2),
    city: () => city(N, rnd),
    house: () => house(N, rnd),
    key: () => key(N, rnd),
    infinity: () => infinity(N, rnd),
  };
  const cache = new Map<Formation, Float32Array>();
  const shape = (f: Formation) => {
    let s = cache.get(f);
    if (!s) cache.set(f, (s = builders[f]()));
    return s;
  };
  const formations = opts.anchors.map((a) => shape(a.formation));

  const geo = new BufferGeometry();
  const pos = new Float32Array(formations[0]);
  geo.setAttribute("position", new BufferAttribute(pos, 3));
  const cols = new Float32Array(N * 3), sizes = new Float32Array(N), seeds = new Float32Array(N), pick = new Uint8Array(N);
  for (let i = 0; i < N; i++) {
    pick[i] = Math.floor(rnd() * DARK_PALETTE.length);
    sizes[i] = (0.4 + rnd() * 1.2) * (rnd() < 0.06 ? 2.6 : 1) * (phone ? 1.25 : 1);
    seeds[i] = rnd() * Math.PI * 2;
  }
  const colorAttr = new BufferAttribute(cols, 3);
  geo.setAttribute("aColor", colorAttr);
  geo.setAttribute("aSize", new BufferAttribute(sizes, 1));
  geo.setAttribute("aSeed", new BufferAttribute(seeds, 1));

  const mat = new ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: { uTime: { value: 0 }, uPx: { value: dpr }, uAlpha: { value: 0.8 } },
    vertexShader: VERTEX,
    fragmentShader: FRAGMENT,
  });
  const points = new Points(geo, mat);
  scene.add(points);

  function setDark(dark: boolean) {
    const palette = (dark ? DARK_PALETTE : LIGHT_PALETTE).map((c) => new Color(c));
    for (let i = 0; i < N; i++) {
      const c = palette[pick[i]];
      cols[i * 3] = c.r; cols[i * 3 + 1] = c.g; cols[i * 3 + 2] = c.b;
    }
    colorAttr.needsUpdate = true;
    // Additive light glows on black but vanishes on a light page.
    mat.blending = dark ? AdditiveBlending : NormalBlending;
    mat.uniforms.uAlpha.value = dark ? 0.95 : 0.45;
    mat.needsUpdate = true;
  }
  setDark(opts.dark);

  /* ── layout: size, and where each section starts on the page ── */
  let tops: number[] = [];
  function layout() {
    const w = canvas.clientWidth || window.innerWidth, h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    // Narrow screens: shrink the shapes so they fit across, keep the points' size.
    points.scale.setScalar(camera.aspect < 1 ? Math.max(0.42, camera.aspect * 1.05) : 1);
    tops = opts.anchors.map((a) => a.el.getBoundingClientRect().top + window.scrollY);
  }
  layout();
  const ro = new ResizeObserver(layout);
  ro.observe(document.body);

  function scrollMix(): [number, number, number] {
    const y = window.scrollY + window.innerHeight * 0.55;
    for (let i = tops.length - 1; i >= 0; i--) {
      if (y >= tops[i]) {
        const next = tops[i + 1] ?? tops[i] + window.innerHeight;
        const m = Math.min(Math.max((y - tops[i]) / (next - tops[i]), 0), 1);
        // hold the shape, then morph in the last 45% of the section
        const k = m < 0.55 ? 0 : (m - 0.55) / 0.45;
        return [i, Math.min(i + 1, formations.length - 1), k * k * (3 - 2 * k)];
      }
    }
    return [0, 0, 0];
  }

  let mx = 0, my = 0, smx = 0, smy = 0;
  const onPointer = (e: PointerEvent) => {
    mx = (e.clientX / window.innerWidth - 0.5) * 2;
    my = (e.clientY / window.innerHeight - 0.5) * 2;
  };
  window.addEventListener("pointermove", onPointer, { passive: true });

  const timer = new Timer();
  function frame() {
    timer.update();
    const t = timer.getElapsed();
    mat.uniforms.uTime.value = t;
    const [a, b, k] = scrollMix();
    const A = formations[a], B = formations[b];
    for (let i = 0; i < N * 3; i++) {
      const target = A[i] + (B[i] - A[i]) * k;
      pos[i] += (target - pos[i]) * 0.045;
    }
    geo.attributes.position.needsUpdate = true;
    smx += (mx - smx) * 0.04;
    smy += (my - smy) * 0.04;
    points.rotation.y = Math.sin(t * 0.1) * 0.12 + smx * 0.16;
    points.rotation.x = smy * 0.1;
    camera.position.x = smx * 0.6;
    camera.position.y = -smy * 0.4;
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
  }

  let active = false;
  return {
    setActive(on) {
      if (on === active) return;
      active = on;
      if (on) timer.reset();
      renderer.setAnimationLoop(on ? frame : null);
    },
    setDark,
    dispose() {
      renderer.setAnimationLoop(null);
      window.removeEventListener("pointermove", onPointer);
      ro.disconnect();
      geo.dispose();
      mat.dispose();
      renderer.dispose();
    },
  };
}
