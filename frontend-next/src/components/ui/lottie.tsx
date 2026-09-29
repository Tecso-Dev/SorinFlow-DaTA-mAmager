"use client";

// A CSP-safe Lottie player: the runtime (the "light" lottie-web build — no
// `eval`, unlike the full build's expressions engine) loads through a
// dynamic import so it never lands in the first JS payload, pauses
// off-screen, and freezes on frame 0 under `prefers-reduced-motion` instead
// of playing. See docs/FRONTEND.md's strict-CSP rules: this never injects a
// `<style>` tag (verified against the light build's source) and needs no
// nonce as a result.

import { useEffect, useRef, useState } from "react";
import { cn } from "cn";
import type { AnimationItem } from "lottie-web/build/player/lottie_light";

function reduceMotionQuery() {
  return window.matchMedia("(prefers-reduced-motion: reduce)");
}

function applyMotionState(anim: AnimationItem, visible: boolean) {
  if (reduceMotionQuery().matches) {
    anim.goToAndStop(0, true);
  } else if (visible) {
    anim.play();
  } else {
    anim.pause();
  }
}

export type LottieProps = {
  /** Parsed Lottie JSON — shape layers only (this panel hand-writes its own, see src/lotties/). */
  animationData: object;
  className?: string;
  /** Wrapper aspect-ratio (width / height); keeps layout stable before the player mounts. */
  aspectRatio?: number;
  loop?: boolean;
  /** Omit for pure decoration (aria-hidden). Set when the animation itself carries meaning. */
  label?: string;
};

/** `renderer="svg"`, paused off-screen, frame-0-only under reduced motion. */
export function Lottie({ animationData, className, aspectRatio = 1, loop = true, label }: LottieProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const animRef = useRef<AnimationItem | null>(null);
  const inViewRef = useRef(false);
  const [inView, setInView] = useState(false);

  // Pause/play as the element enters or leaves the viewport, so a long page
  // never runs every section's animation at once.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.01 });
    io.observe(host);
    return () => io.disconnect();
  }, []);

  // Load the player (dynamic import) and the animation once; destroy on unmount.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let cancelled = false;

    import("lottie-web/build/player/lottie_light").then(({ default: lottie }) => {
      if (cancelled || !host) return;
      const anim = lottie.loadAnimation({
        container: host,
        renderer: "svg",
        loop,
        autoplay: false,
        animationData,
      });
      animRef.current = anim;
      applyMotionState(anim, inViewRef.current);
    });

    return () => {
      cancelled = true;
      animRef.current?.destroy();
      animRef.current = null;
    };
    // animationData/loop are treated as fixed for the lifetime of one <Lottie>: swap the `key` to load a different clip.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply play/pause whenever visibility changes, and once more if the
  // user's reduced-motion preference flips mid-session.
  useEffect(() => {
    inViewRef.current = inView;
    const anim = animRef.current;
    if (anim) applyMotionState(anim, inView);
  }, [inView]);

  useEffect(() => {
    const mql = reduceMotionQuery();
    const onChange = () => {
      const anim = animRef.current;
      if (anim) applyMotionState(anim, inViewRef.current);
    };
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return (
    <div
      ref={hostRef}
      className={cn("w-full [&_svg]:block", className)}
      style={{ aspectRatio: String(aspectRatio) }}
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
    />
  );
}
