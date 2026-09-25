"use client";

// The landing page's 3D backdrop. A static «شب نیلی» gradient is always
// there (it is all that shows without WebGL or under reduced motion); the
// particle scene is fetched with a dynamic import once the browser is idle,
// fades in over it, and stops drawing when it is off screen or the tab is
// hidden.

import { useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";
import type { Formation, NebulaHandle } from "./nebula-scene";

export type NebulaAnchor = { id: string; formation: Formation };

const idle = (fn: () => void) => {
  if ("requestIdleCallback" in window) {
    const h = window.requestIdleCallback(fn, { timeout: 2500 });
    return () => window.cancelIdleCallback(h);
  }
  const h = setTimeout(fn, 400);
  return () => clearTimeout(h);
};

export function Nebula({ anchors }: { anchors: NebulaAnchor[] }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const reduce = useReducedMotion();
  const [live, setLive] = useState(false);
  const plan = anchors.map((a) => `${a.id}:${a.formation}`).join(",");

  useEffect(() => {
    const el = canvas.current;
    if (!el || reduce) return;
    let handle: NebulaHandle | null = null;
    let cancelled = false;
    let onScreen = true;
    const html = document.documentElement;
    const update = () => handle?.setActive(onScreen && !document.hidden);

    const io = new IntersectionObserver(([e]) => {
      onScreen = e.isIntersecting;
      update();
    });
    io.observe(el);
    const themeWatch = new MutationObserver(() => handle?.setDark(html.classList.contains("dark")));
    themeWatch.observe(html, { attributes: true, attributeFilter: ["class"] });
    document.addEventListener("visibilitychange", update);

    const stopIdle = idle(async () => {
      try {
        const { startNebula } = await import("./nebula-scene");
        if (cancelled) return;
        const els = plan.split(",").map((s) => {
          const [id, formation] = s.split(":");
          return { el: document.getElementById(id), formation: formation as Formation };
        });
        handle = startNebula(el, {
          anchors: els.filter((a): a is { el: HTMLElement; formation: Formation } => !!a.el),
          dark: html.classList.contains("dark"),
        });
        update();
        setLive(true);
      } catch {
        // No WebGL (or the chunk failed): the gradient stays, which is fine.
      }
    });

    return () => {
      cancelled = true;
      stopIdle();
      io.disconnect();
      themeWatch.disconnect();
      document.removeEventListener("visibilitychange", update);
      handle?.dispose();
    };
  }, [plan, reduce]);

  return (
    <div aria-hidden className="pointer-events-none sticky top-0 -mb-[100dvh] h-dvh w-full overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_22%_28%,rgb(99_102_241/0.28),transparent_70%),radial-gradient(ellipse_45%_40%_at_78%_72%,rgb(139_92_246/0.22),transparent_70%),radial-gradient(ellipse_30%_25%_at_60%_20%,rgb(34_211_238/0.12),transparent_70%)] dark:bg-[radial-gradient(ellipse_60%_50%_at_22%_28%,rgb(99_102_241/0.32),transparent_70%),radial-gradient(ellipse_45%_40%_at_78%_72%,rgb(139_92_246/0.26),transparent_70%),radial-gradient(ellipse_30%_25%_at_60%_20%,rgb(34_211_238/0.16),transparent_70%)]" />
      <canvas
        ref={canvas}
        data-testid="nebula"
        data-live={live ? "1" : "0"}
        className={cn("absolute inset-0 size-full transition-opacity duration-[1.4s]", live ? "opacity-100" : "opacity-0")}
      />
      <div className="absolute inset-0 hidden bg-[radial-gradient(ellipse_90%_75%_at_50%_45%,transparent_55%,rgb(0_0_0/0.7)_100%)] dark:block" />
    </div>
  );
}
