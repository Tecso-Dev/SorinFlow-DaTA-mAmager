// Geometry for the Simorgh logo concepts (د, هـ, و in logos.tsx): a tiny
// faceted 3D renderer that runs once, when the module loads. Shapes are
// modelled as ribbons and low-poly solids, lit by one light from the top left,
// sorted back to front and flattened into plain SVG polygons with solid fills.
// No React here, so the same numbers render on the server and in the browser.

export type V3 = [number, number, number];
export type Pt = [number, number];
/** One thing to draw: a flat facet (`fill`) or a highlight line (`line`,
 *  white, with its opacity). */
export type Facet = { d: string; fill: string; line?: undefined } | { d: string; fill?: undefined; line: number };

/* ───────────────────────── vectors ───────────────────────── */

const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const unit = (a: V3): V3 => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const clamp = (v: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

/**
 * Round to `dp` decimals, the same way in every JavaScript engine.
 *
 * This art is computed twice — once in Node when the page is server-rendered,
 * once in the browser — and the two must agree to the character or React
 * reports a hydration mismatch and keeps the server's attribute. Math.cos,
 * Math.sin and Math.pow are only required to be *nearly* the same everywhere,
 * so a plain Math.round(n * 100) flips whenever a value sits within a hair of
 * a .005 boundary; over tens of thousands of numbers that is a certainty.
 * Rounding to two extra decimals first lands both engines on exactly the same
 * value, and the second rounding then cannot disagree.
 */
export function round(n: number, dp: number) {
  const coarse = 10 ** (dp + 2);
  const k = 10 ** dp;
  return Math.round((Math.round(n * coarse) / coarse) * k) / k;
}

export const r2 = (n: number) => round(n, 2);

/* ───────────────────────── light and colour ───────────────────────── */

// Picture space: x to the right, y down (as in SVG), z toward the viewer.
// One light, from the top left and a little in front.
const LIGHT = unit([-0.5, -0.62, 0.6]);
const HALF = unit(add(LIGHT, [0, 0, 1]));

/** Colour stops from shadow to highlight. */
export type Ramp = readonly string[];

export const RAMPS = {
  indigo: ["#1e1b4b", "#312e81", "#3730a3", "#4338ca", "#4f46e5", "#6366f1", "#818cf8", "#a5b4fc", "#c7d2fe", "#e0e7ff"],
  violet: ["#2e1065", "#3b0764", "#4c1d95", "#5b21b6", "#6d28d9", "#7c3aed", "#8b5cf6", "#a78bfa", "#c4b5fd", "#ddd6fe"],
  cyan: ["#083344", "#164e63", "#155e75", "#0e7490", "#0891b2", "#06b6d4", "#22d3ee", "#67e8f9", "#a5f3fc", "#cffafe"],
  saffron: ["#451a03", "#78350f", "#92400e", "#b45309", "#d97706", "#f59e0b", "#fbbf24", "#fcd34d", "#fde68a", "#fef3c7"],
} as const satisfies Record<string, Ramp>;

const hex2rgb = (h: string): V3 => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
const rgb2hex = (c: V3) => `#${c.map((v) => clamp(round(v, 0), 0, 255).toString(16).padStart(2, "0")).join("")}`;

/** The colour at `x` (0 = deepest shadow, 1 = brightest light) along a ramp. */
export function rampAt(ramp: Ramp, x: number): V3 {
  const p = clamp(x || 0) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(p));
  const a = hex2rgb(ramp[i]), b = hex2rgb(ramp[i + 1]);
  const t = p - i;
  return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
}

export const mix = (a: V3, b: V3, t: number): V3 => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];

/** How a surface facing `n` (unit, toward the viewer) is lit: a soft
 *  "half Lambert" diffuse term and a small specular glint. */
export function light(n: V3) {
  // clamp first: rounding can push the dot product just past ±1, and a
  // fractional power of a negative number is NaN, which spreads into colours
  const diffuse = Math.pow(clamp(0.5 + 0.5 * dot(n, LIGHT)), 1.35);
  const glint = Math.pow(clamp(dot(n, HALF)), 28);
  return { diffuse, glint };
}

/** Paints a facet: which way it faces, whether we see its back, and where it
 *  sits along its object (0..1), to a colour. */
export type Paint = (lit: { diffuse: number; glint: number }, back: boolean, along: number) => V3;

/** A ramp for the front, another for the back, the glint added as white. */
export function twoSided(front: (along: number) => Ramp, back: (along: number) => Ramp, lo = 0.12, hi = 0.92): Paint {
  return ({ diffuse, glint }, isBack, along) => {
    const base = rampAt(isBack ? back(along) : front(along), lerp(lo, hi, diffuse));
    return mix(base, [255, 255, 255], glint * 0.55);
  };
}

/** Blend two ramps along the object. */
export function blendRamp(a: Ramp, b: Ramp, t: number): Ramp {
  return a.map((c, i) => rgb2hex(mix(hex2rgb(c), hex2rgb(b[Math.min(i, b.length - 1)]), clamp(t))));
}

/* ───────────────────────── facets ───────────────────────── */

type Face = { pts: V3[]; color: V3; z: number; layer: number; line?: number };

// The light's direction as seen on the picture (toward it).
const LIGHT2 = (() => {
  const l = Math.hypot(LIGHT[0], LIGHT[1]);
  return [LIGHT[0] / l, LIGHT[1] / l] as Pt;
})();

function faceOf(pts: V3[], normal: V3, paint: Paint, along: number, layer: number, cull: boolean): Face | null {
  let n = unit(normal);
  const back = n[2] < 0;
  if (back) {
    if (cull) return null;
    n = mul(n, -1);
  }
  // rounded, because render() sorts on this: two engines that disagree in the
  // last bit would otherwise paint the facets in a different order
  const z = round(pts.reduce((s, p) => s + p[2], 0) / pts.length, 3);
  return { pts, color: paint(light(n), back, along), z, layer };
}

/** A scene: faces from several objects, drawn back to front within a layer. */
export class Scene {
  private faces: Face[] = [];

  add(f: Face | null) {
    if (f) this.faces.push(f);
  }

  /** A flat band along a curve. `roll` turns the band about its own centre
   *  line: 0 keeps it in the picture plane, π/2 shows it edge on. */
  ribbon(o: {
    at: (t: number) => V3;
    n: number;
    width: (t: number) => number;
    roll?: (t: number) => number;
    /** Shift the band sideways from its centre line (−1..1 of its width). */
    offset?: (t: number) => number;
    paint: Paint;
    layer?: number;
    t0?: number;
    t1?: number;
    /** Opacity of the thin white line where an edge of the band faces the light. */
    rim?: number;
  }) {
    const { at, n, width, roll = () => 0, offset = () => 0, paint, layer = 0, t0 = 0, t1 = 1, rim = 0 } = o;
    const L: V3[] = [], R: V3[] = [];
    const h = (t1 - t0) / n / 4;
    for (let i = 0; i <= n; i++) {
      const t = lerp(t0, t1, i / n);
      const c = at(t);
      const T = unit(sub(at(t + h), at(t - h)));
      const N0 = unit(cross(T, [0, 0, 1]));
      const B0 = cross(N0, T);
      const r = roll(t);
      const W = add(mul(N0, Math.cos(r)), mul(B0, Math.sin(r)));
      const w = width(t);
      const mid = add(c, mul(W, offset(t) * w));
      L.push(add(mid, mul(W, w / 2)));
      R.push(sub(mid, mul(W, w / 2)));
    }
    const zs: number[] = [];
    for (let i = 0; i < n; i++) {
      const q = [L[i], L[i + 1], R[i + 1], R[i]];
      const normal = cross(sub(L[i + 1], R[i]), sub(R[i + 1], L[i]));
      this.add(faceOf(q, normal, paint, (i + 0.5) / n, layer, false));
      zs.push((L[i][2] + L[i + 1][2] + R[i][2] + R[i + 1][2]) / 4);
    }
    if (rim > 0) {
      this.rims(L, R, zs, rim, layer);
      this.rims(R, L, zs, rim, layer);
    }
    return { left: L, right: R };
  }

  /** Short runs of highlight along `edge` where it faces the light. Each run
   *  is sorted just after the facets it lies on, so a strand passing in front
   *  still hides it. */
  private rims(edge: V3[], other: V3[], zs: number[], opacity: number, layer: number) {
    const facing: number[] = [];
    for (let i = 0; i < edge.length - 1; i++) {
      const dx = edge[i + 1][0] - edge[i][0], dy = edge[i + 1][1] - edge[i][1];
      const l = Math.hypot(dx, dy) || 1;
      let nx = -dy / l, ny = dx / l;
      const ox = edge[i][0] - other[i][0], oy = edge[i][1] - other[i][1];
      if (nx * ox + ny * oy < 0) {
        nx = -nx;
        ny = -ny;
      }
      facing.push(clamp((nx * LIGHT2[0] + ny * LIGHT2[1] - 0.25) / 0.6));
    }
    let i = 0;
    while (i < facing.length) {
      if (facing[i] <= 0.02) {
        i++;
        continue;
      }
      const start = i;
      let sum = 0, zmax = -Infinity;
      while (i < facing.length && facing[i] > 0.02 && i - start < 8) {
        sum += facing[i];
        zmax = Math.max(zmax, zs[i]);
        i++;
      }
      this.faces.push({
        pts: edge.slice(start, i + 1),
        color: [255, 255, 255],
        z: round(zmax + 0.01, 3),
        layer,
        line: round((opacity * sum) / (i - start), 3),
      });
    }
  }

  /** A closed low-poly solid of revolution around a curved axis. */
  spindle(o: {
    at: (s: number) => V3;
    radius: (s: number) => number;
    rings: number;
    sides: number;
    /** Squash the section: 1 = round, < 1 flatter toward the viewer. */
    depth?: number;
    spin?: number;
    paint: Paint;
    layer?: number;
  }) {
    const { at, radius, rings, sides, depth = 1, spin = 0, paint, layer = 0 } = o;
    const ring: V3[][] = [];
    const axis: V3[] = [];
    for (let i = 0; i <= rings; i++) {
      const s = i / rings;
      const c = at(s);
      axis.push(c);
      const T = unit(sub(at(Math.min(1, s + 0.01)), at(Math.max(0, s - 0.01))));
      const N0 = unit(cross(T, [0, 0, 1]));
      const B0 = cross(N0, T);
      const r = radius(s);
      const pts: V3[] = [];
      for (let k = 0; k < sides; k++) {
        const a = spin + (k / sides) * Math.PI * 2;
        pts.push(add(c, add(mul(N0, Math.cos(a) * r), mul(B0, Math.sin(a) * r * depth))));
      }
      ring.push(pts);
    }
    for (let i = 0; i < rings; i++) {
      for (let k = 0; k < sides; k++) {
        const k1 = (k + 1) % sides;
        const a = ring[i][k], b = ring[i][k1], c = ring[i + 1][k1], d = ring[i + 1][k];
        const centre = mul(add(add(a, b), add(c, d)), 0.25);
        const mid = mul(add(axis[i], axis[i + 1]), 0.5);
        let normal = cross(sub(c, a), sub(d, b));
        if (dot(normal, sub(centre, mid)) < 0) normal = mul(normal, -1);
        const quad = radius(i / rings) < 1e-3 ? [a, c, d] : radius((i + 1) / rings) < 1e-3 ? [a, b, c] : [a, b, c, d];
        this.add(faceOf(quad, normal, paint, (i + 0.5) / rings, layer, true));
      }
    }
  }

  /** A flat polygon (a facet you place by hand), lit by its own normal. */
  polygon(pts: V3[], paint: Paint, along = 0.5, layer = 0) {
    const normal = cross(sub(pts[1], pts[0]), sub(pts[2], pts[0]));
    this.add(faceOf(pts, normal, paint, along, layer, false));
  }

  /** Every facet, back to front, as SVG paths. */
  render(): Facet[] {
    return this.faces
      .map((f, i) => ({ f, i }))
      .sort((a, b) => a.f.layer - b.f.layer || a.f.z - b.f.z || a.i - b.i)
      .map(({ f }): Facet => {
        const pts = f.pts.map(([x, y]) => [x, y] as Pt);
        return f.line === undefined ? { d: path2(pts, true), fill: rgb2hex(f.color) } : { d: path2(pts), line: r2(f.line) };
      });
  }
}

export function path2(ps: Pt[], close = false) {
  return `M${ps.map(([x, y]) => `${r2(x)} ${r2(y)}`).join("L")}${close ? "Z" : ""}`;
}

/* ───────────────────────── curves ───────────────────────── */

/** A point on a cubic Bézier. */
export function bez(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: number): Pt {
  const u = 1 - t;
  return [
    u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
  ];
}

/** A chain of cubic Béziers as one curve over 0..1. */
export function chain(segs: [Pt, Pt, Pt, Pt][]) {
  return (t: number): Pt => {
    const x = clamp(t) * segs.length;
    const i = Math.min(segs.length - 1, Math.floor(x));
    const [a, b, c, d] = segs[i];
    return bez(a, b, c, d, x - i);
  };
}

export const smooth = (t: number) => t * t * (3 - 2 * t);

/** A curve sampled into an SVG polyline, and its length (for a running light). */
export function trace(at: (t: number) => Pt, n: number, t0 = 0, t1 = 1) {
  const ps: Pt[] = [];
  let length = 0;
  for (let i = 0; i <= n; i++) {
    const p = at(lerp(t0, t1, i / n));
    if (i) length += Math.hypot(p[0] - ps[i - 1][0], p[1] - ps[i - 1][1]);
    ps.push(p);
  }
  return { d: path2(ps), length: round(length, 1) };
}

/** An ellipse turned along a direction (a feather's eye, a pupil). */
export type Dot = { cx: number; cy: number; rx: number; ry: number; rot: number; fill: string };

function dotAlong(at: (t: number) => Pt, t: number, rx: number, ry: number, fill: string): Dot {
  const [x, y] = at(t);
  const [x1, y1] = at(Math.min(1, t + 0.02));
  const [x0, y0] = at(Math.max(0, t - 0.02));
  const rot = (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI - 90;
  return { cx: r2(x), cy: r2(y), rx: r2(rx), ry: r2(ry), rot: round(rot, 0), fill };
}

/* ───────────────────────── the Simorgh's parts ───────────────────────── */

const flat = (x: number, y: number, z = 0): V3 => [x, y, z];

/** A rounded taper to zero over `v` in 0..1 — clamped, because a fractional
 *  power of a cosine that rounding pushed below zero is NaN. */
const taper = (v: number, power = 0.8) => Math.pow(Math.max(0, Math.cos(clamp(v) * (Math.PI / 2))), power);

/** A long neck rising from the shoulders into a head with a hooked beak,
 *  facing left, into the light. `o` moves it, `k` scales it. */
function headAndNeck(s: Scene, o: Pt, k: number, layer: number) {
  const P = (x: number, y: number): Pt => [o[0] + x * k, o[1] + y * k];
  const neck = chain([
    [P(0, 0), P(0.6, -5.5), P(2.4, -10.2), P(0.6, -14.6)],
    [P(0.6, -14.6), P(-1, -17.8), P(-5.2, -17.6), P(-8, -14.6)],
  ]);
  s.spindle({
    at: (u) => flat(...neck(u), 7),
    radius: (u) => k * (u < 0.5 ? 2.4 + 0.5 * Math.sin(u * Math.PI) : 3.1 * taper((u - 0.5) / 0.5, 0.75)),
    rings: 12,
    sides: 7,
    depth: 0.85,
    paint: twoSided(() => RAMPS.indigo, () => RAMPS.indigo, 0.28, 0.9),
    layer,
    spin: 0.5,
  });
  crestOf(s, [o[0] + 0.2 * k, o[1] - 17.2 * k], k, 1, layer - 1);
  const eye = P(-3.4, -16.2);
  return { eye: { cx: r2(eye[0]), cy: r2(eye[1]), rx: r2(0.8 * k), ry: r2(0.8 * k), rot: 0, fill: "#fcd34d" } as Dot };
}

/** Three cyan plumes sweeping back from a crown at `o`, `dir` = +1 to the
 *  right (the head faces left), −1 mirrored. */
function crestOf(s: Scene, o: Pt, k: number, dir: number, layer: number) {
  const P = (x: number, y: number): Pt => [o[0] + x * k * dir, o[1] + y * k];
  const crest = [
    chain([[P(0, -0.4), P(1.2, -4.4), P(4.6, -7), P(8.6, -6.6)]]),
    chain([[P(0.8, 0.2), P(2.8, -2.8), P(6.2, -3.6), P(10, -2.4)]]),
    chain([[P(1.4, 1.2), P(3.4, -0.2), P(6.4, 0), P(9.2, 1.6)]]),
  ];
  const plume = twoSided(() => RAMPS.cyan, () => RAMPS.violet, 0.3, 0.86);
  crest.forEach((c, i) =>
    s.ribbon({ at: (u) => flat(...c(u), 6.5 - i * 0.2), n: 10, width: (u) => k * (1.7 * (1 - u) + 0.3), roll: () => 0.5 * dir, paint: plume, layer }),
  );
}

/** The body: a faceted teardrop, chest high, from `bottom` up `h`. */
function bodyOf(s: Scene, x: number, bottom: number, h: number, r: number, layer: number) {
  const prof = (u: number) => (Math.pow(u, 1.2) * Math.pow(1 - u, 0.75)) / (Math.pow(0.615, 1.2) * Math.pow(0.385, 0.75));
  s.spindle({
    at: (u) => [x, bottom - u * h, 6],
    radius: (u) => r * Math.pow(Math.max(0, prof(u)), 0.8),
    rings: 9,
    sides: 8,
    depth: 0.8,
    paint: twoSided(() => RAMPS.indigo, () => RAMPS.indigo, 0.2, 0.88),
    layer,
    spin: 0.2,
  });
}

/** Three tail plumes, the outer two curling out like a lyre, each with a
 *  saffron eye (the Simorgh's peacock tail). */
function tailOf(s: Scene, plumes: ((t: number) => Pt)[], width: number, rim: number) {
  const vane = (u: number) => width * (0.3 + 0.7 * Math.sin(Math.min(1, u * 1.08) * Math.PI) ** 0.9);
  const paint = twoSided((u) => blendRamp(RAMPS.violet, RAMPS.cyan, smooth(u) * 0.7), () => RAMPS.violet, 0.16, 0.84);
  plumes.forEach((c, i) =>
    s.ribbon({
      at: (u) => flat(...c(u), 4 - (i ? 0.6 : 0)),
      n: 14,
      width: (u) => vane(u) * (i ? 0.85 : 1),
      roll: (u) => (i === 1 ? -0.7 : i === 2 ? 0.7 : 0.25) * u,
      paint,
      rim,
    }),
  );
  return plumes.flatMap((c) => [dotAlong(c, 0.74, 1.3, 1.75, "#fbbf24"), dotAlong(c, 0.74, 0.55, 0.85, "#1e1b4b")]);
}

/* ═════════════════ (د) wings that sweep round into an infinity ═════════════════ */
// The ∞ is a lemniscate whose two lobes are the wings, each tipped up a
// little like raised wings. The band's top surface faces the sky on the
// leading (upper) strands and turns down on the trailing (lower) ones, so the
// upper edges catch the light. The body stands on the crossing and hides it.

export const WINGS_ART = (() => {
  const cx = 32, cy = 33, a = 29.5, ky = 1, tilt = 0.14;
  const lem = (t: number) => {
    const k = 1 + Math.sin(t) ** 2;
    return [Math.cos(t) / k, (Math.sin(t) * Math.cos(t)) / k] as const;
  };
  const at2 = (t: number): Pt => {
    const [X, Y] = lem(t);
    const th = X >= 0 ? tilt : -tilt;
    const x = X * a, y = Y * a * ky;
    return [cx + x * Math.cos(th) - y * Math.sin(th), cy - (x * Math.sin(th) + y * Math.cos(th))];
  };
  const at = (u: number): V3 => {
    const t = u * Math.PI * 2;
    return [...at2(t), 2.6 * Math.sin(t)];
  };
  // 1 on the leading strands, 0 on the trailing ones, blended round the tips
  const upper = (t: number) => smooth(clamp(0.5 + lem(t)[1] / 0.24));
  const width = (u: number) => {
    const t = u * Math.PI * 2, ax = Math.abs(lem(t)[0]);
    const lead = 8.5 * (0.7 + 0.3 * Math.sin(Math.PI * Math.min(1, ax * 1.1)));
    const trail = 5.2 * (0.8 + 0.2 * Math.sin(Math.PI * ax));
    return trail + (lead - trail) * upper(t);
  };
  const roll = (u: number) => {
    const t = u * Math.PI * 2;
    return (lem(t)[0] >= 0 ? 1 : -1) * (-0.7 + 1.5 * upper(t));
  };
  const side = (u: number) => 0.5 - 0.5 * Math.cos(u * Math.PI * 2); // 0 right tip … 1 left tip
  const paint = twoSided((u) => blendRamp(RAMPS.violet, RAMPS.indigo, side(u)), () => RAMPS.violet, 0.14, 0.8);
  // Each lobe is built on its own, so the two wings can beat separately.
  const lobe = (t0: number, t1: number) => {
    const sc = new Scene();
    sc.ribbon({ at, n: 64, width, roll, rim: 0.75, paint, t0: t0 / (Math.PI * 2), t1: t1 / (Math.PI * 2) });
    return sc.render();
  };
  const right = [...lobe(-Math.PI / 2, 0), ...lobe(0, Math.PI / 2)];
  const left = lobe(Math.PI / 2, (3 * Math.PI) / 2);

  const tail = new Scene();
  const eyes = tailOf(tail, [
    chain([[[32, 42], [32, 48], [31, 53], [32, 61]]]),
    chain([[[31.4, 42], [29.5, 48], [24.5, 52], [21.2, 58.5]]]),
    chain([[[32.6, 42], [34.5, 48], [39.5, 52], [42.8, 58.5]]]),
  ], 4.8, 0.5);

  const bird = new Scene();
  bodyOf(bird, 32, 45, 21, 5.2, 1);
  const head = headAndNeck(bird, [32, 27], 1, 2);

  const spine = trace((u) => at2(u * Math.PI * 2), 96);
  return {
    /** The two wings apart, so each can beat around the body. */
    wingRight: right,
    wingLeft: left,
    tail: tail.render(),
    bird: bird.render(),
    dots: [...eyes, head.eye],
    spine: spine.d,
    spineLength: spine.length,
    pivot: [cx, cy] as Pt,
  };
})();

/* ═════════════════ (هـ) the ribbon that is an ∞ and an S ═════════════════ */
// One strip of ribbon coiled two and a half times while its centre slides
// down: the upper coil and the lower coil close, they cross in the middle
// (the ∞) and the whole diagonal reads as an S. It runs from front to back,
// so every overlap is unambiguous. The strip leaves the figure at the top as
// a neck and a head, and at the bottom as three tail feathers.

export const RIBBON_ART = (() => {
  const c1: Pt = [31.5, 18.5], c2: Pt = [32.5, 45.5], r = 12.6, turns = 2.5, th0 = (-96 * Math.PI) / 180;
  const slide = (u: number) => smooth(clamp((u - 0.28) / 0.44));
  // u = 0 is the head, at the top of the upper coil; u = 1 the tail, below
  const at2 = (u: number): Pt => {
    const m = slide(u);
    const th = th0 + turns * Math.PI * 2 * u;
    return [lerp(c1[0], c2[0], m) + r * Math.cos(th), lerp(c1[1], c2[1], m) + r * Math.sin(th)];
  };
  const at = (u: number): V3 => [...at2(u), 3.4 * Math.cos(u * Math.PI)];
  const width = (u: number) => {
    const head = smooth(clamp((0.12 - u) / 0.12)); // narrows into the neck
    const tail = smooth(clamp((u - 0.9) / 0.1));
    return 8.4 * (1 - 0.5 * head - 0.26 * tail);
  };
  // a slow half turn of the strip along its length: the upper coil shows its
  // lit face, the lower one its shaded back
  const roll = (u: number) => -0.5 + 1.15 * smooth(clamp((u - 0.22) / 0.56));

  const band = new Scene();
  band.ribbon({
    at, n: 150, width, roll, rim: 0.8,
    paint: twoSided((u) => blendRamp(RAMPS.indigo, RAMPS.violet, smooth(u)), () => RAMPS.violet, 0.14, 0.84),
  });

  const bird = new Scene();
  // the neck leaves the top of the coil and turns the head back to the left
  const h0 = at2(0), h1 = at2(0.016);
  const dir = Math.atan2(h0[1] - h1[1], h0[0] - h1[0]);
  const head = (v: number): Pt => {
    const a = dir + 1.35 * smooth(v);
    const L = 15 * v;
    return [h0[0] + (Math.cos(dir) * 0.5 + Math.cos(a) * 0.5) * L, h0[1] + (Math.sin(dir) * 0.5 + Math.sin(a) * 0.5) * L - 1.6 * v];
  };
  const rHead = width(0) / 2;
  bird.spindle({
    at: (v) => flat(...head(v), 6),
    radius: (v) => (v < 0.42 ? rHead + (4.3 - rHead) * Math.sin((v / 0.42) * (Math.PI / 2)) : 4.3 * taper((v - 0.42) / 0.58)),
    rings: 11, sides: 7, depth: 0.85, paint: twoSided(() => RAMPS.indigo, () => RAMPS.indigo, 0.3, 0.92), layer: 2, spin: 0.4,
  });
  crestOf(bird, head(0.3), 0.82, 1, 1);

  const tail = new Scene();
  const t0 = at2(1), t1 = at2(1 - 0.016);
  const tdir = Math.atan2(t0[1] - t1[1], t0[0] - t1[0]);
  const plumes = [-1, 0, 1].map((k) => (v: number): Pt => {
    const a = tdir + k * 0.44 + 0.34;
    return [t0[0] + Math.cos(a) * 17 * v, t0[1] + Math.sin(a) * 17 * v];
  });
  const eyes = tailOf(tail, plumes, 4.6, 0.5);

  const spine = trace(at2, 120);
  const eye = head(0.46);
  return {
    band: band.render(),
    bird: bird.render(),
    tail: tail.render(),
    dots: [...eyes, { cx: r2(eye[0] - 1), cy: r2(eye[1] - 1.2), rx: 0.85, ry: 0.85, rot: 0, fill: "#fcd34d" } as Dot],
    spine: spine.d,
    spineLength: spine.length,
  };
})();

/* ═════════════════ (و) wings spread over an isometric house ═════════════════ */
// The Simorgh stands over a small isometric house with its wings out: the
// wings are the roof (the property, and the protection over it). A little ∞
// sits in the tail feathers.

/** Isometric projection: x to the lower right, y to the lower left, z up. */
function iso(ox: number, oy: number, s: number) {
  return (x: number, y: number, z: number): V3 => [
    ox + (x - y) * Math.cos(Math.PI / 6) * s,
    oy + (x + y) * 0.5 * s - z * s,
    // toward the viewer: the nearer corner of the plan, raised by height
    (x + y) * 0.5 + z * 0.4,
  ];
}

export const HOUSE_ART = (() => {
  const house = new Scene();
  const L = 17, D = 17, H = 10;
  const p = iso(32, 44, 1.12);
  const wallPaint = (lit: number) =>
    twoSided(() => blendRamp(RAMPS.indigo, RAMPS.violet, lit), () => RAMPS.indigo, 0.3, 0.95);
  // the flat top the wings shelter: in shadow, so the wings read as the roof
  house.polygon([p(0, 0, H), p(L, 0, H), p(L, D, H), p(0, D, H)], twoSided(() => RAMPS.indigo, () => RAMPS.indigo, 0.06, 0.3), 0.5, 0);
  // four walls, drawn as flat quads so each catches its own share of light
  house.polygon([p(0, D, 0), p(L, D, 0), p(L, D, H), p(0, D, H)], wallPaint(0.15), 0.5, 0);
  house.polygon([p(L, D, 0), p(L, 0, 0), p(L, 0, H), p(L, D, H)], wallPaint(0.7), 0.5, 0);
  // the ground slab the house stands on
  const slab = twoSided(() => RAMPS.violet, () => RAMPS.violet, 0.1, 0.4);
  house.polygon([p(-3.5, -3.5, 0), p(L + 3.5, -3.5, 0), p(L + 3.5, D + 3.5, 0), p(-3.5, D + 3.5, 0)], slab, 0.5, -1);

  // The door and two lit windows are their own group: at favicon sizes the
  // mark drops them and keeps only the silhouette.
  const openings = new Scene();
  const door = twoSided(() => RAMPS.cyan, () => RAMPS.cyan, 0.45, 1);
  openings.polygon([p(6, D, 0), p(10.4, D, 0), p(10.4, D, 6), p(6, D, 6)], door, 0.5, 1);
  openings.polygon([p(1.8, D, 6.6), p(4.6, D, 6.6), p(4.6, D, 9.2), p(1.8, D, 9.2)], door, 0.5, 1);
  openings.polygon([p(12.2, D, 6.6), p(15, D, 6.6), p(15, D, 9.2), p(12.2, D, 9.2)], door, 0.5, 1);

  const bird = new Scene();
  // Each wing is four feathers fanned from the shoulder, sloping down and out
  // until their tips reach the top corners of the house: the two wings are
  // the pitched roof over it.
  const shoulder: Pt = [32, 28];
  const feather = (side: number, i: number) => (v: number): V3 => {
    const len = 22 - i * 1.4;
    const x = shoulder[0] + side * len * v * (0.98 - 0.06 * i);
    const y = shoulder[1] - 2.6 * Math.sin(v * Math.PI) + (0.56 + i * 0.09) * len * v;
    return [x, y, 7 - i * 1.1 - 2.4 * v];
  };
  const wingPaint = (i: number) =>
    twoSided(
      (u) => blendRamp(RAMPS.indigo, RAMPS.violet, 0.25 + 0.2 * i + 0.4 * u),
      () => RAMPS.violet,
      0.16, 0.88 - i * 0.05,
    );
  for (const side of [-1, 1]) {
    for (let i = 3; i >= 0; i--) {
      bird.ribbon({
        at: feather(side, i),
        n: 26,
        width: (v) => (5.4 - i * 0.5) * (0.45 + 0.55 * Math.sin(Math.min(1, v * 1.1) * Math.PI) ** 0.7) + 0.6,
        roll: () => side * (0.5 + i * 0.1),
        paint: wingPaint(i),
        rim: 0.75,
        layer: 2 - (i === 0 ? 0 : 1),
      });
    }
  }
  // the body, standing over the ridge, and the head above it
  bodyOf(bird, 32, 37, 14, 4, 3);
  const head = headAndNeck(bird, [32, 26], 0.72, 4);

  const tail = new Scene();
  // the tail: two feathers either side of a small ∞
  const tp = twoSided((u) => blendRamp(RAMPS.violet, RAMPS.cyan, smooth(u) * 0.7), () => RAMPS.violet, 0.16, 0.84);
  for (const k of [-1, 1]) {
    tail.ribbon({
      at: (v) => [32 + k * (2.4 + 4.2 * v), 36 + 12 * v + 2 * v * v, 3],
      n: 10, width: (v) => 3.2 * (0.3 + 0.7 * Math.sin(Math.min(1, v * 1.05) * Math.PI) ** 0.8) + 0.2,
      roll: () => 0.35 * k, paint: tp, layer: 1, rim: 0.4,
    });
  }
  // the little ∞ the tail ends in, over the wall below
  const inf = (u: number): Pt => {
    const t = u * Math.PI * 2, k = 1 + Math.sin(t) ** 2;
    return [32 + (7 * Math.cos(t)) / k, 51.5 + (7 * 1.5 * Math.sin(t) * Math.cos(t)) / k];
  };
  tail.ribbon({
    at: (u) => flat(...inf(u), 2 * Math.sin(u * Math.PI * 2)),
    n: 60, width: () => 2.2, roll: () => 0.4,
    paint: twoSided(() => RAMPS.cyan, () => RAMPS.violet, 0.3, 0.95), layer: 2, rim: 0.7,
  });

  const infinity = trace(inf, 72);
  return {
    house: house.render(),
    openings: openings.render(),
    bird: bird.render(),
    tail: tail.render(),
    dots: [head.eye],
    infinity: infinity.d,
    infinityLength: infinity.length,
  };
})();

/* ───────────────────────── the ∞ loader's path ───────────────────────── */

/** The lemniscate used by the page loader, in a 120×60 box. */
export const LOADER = (() => {
  const at = (u: number): Pt => {
    const t = u * Math.PI * 2, k = 1 + Math.sin(t) ** 2;
    return [60 + (50 * Math.cos(t)) / k, 30 + (50 * 1.4 * Math.sin(t) * Math.cos(t)) / k];
  };
  const { d, length } = trace(at, 160);
  return { d, length };
})();

export { add, sub, mul, dot, cross, unit, lerp, clamp };
