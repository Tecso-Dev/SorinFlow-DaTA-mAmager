"use client";

import { useSyncExternalStore } from "react";

// Does this visitor want things to hold still?
//
// motion's own useReducedMotion is right for anything that only feeds an
// `animate` prop, which the global <MotionConfig reducedMotion="user"> already
// takes care of. This one is for the cases where the answer changes what is
// *rendered* — a pinned section that becomes a plain list, a moving dot that
// is simply not drawn. The server cannot know the preference, so the snapshot
// it renders is always `false` and the real answer arrives on the render right
// after hydration; branching on it any earlier hands React a tree that does
// not match the HTML it is hydrating (React error #418).

const QUERY = "(prefers-reduced-motion: reduce)";

const subscribe = (onChange: () => void) => {
  const m = window.matchMedia(QUERY);
  m.addEventListener("change", onChange);
  return () => m.removeEventListener("change", onChange);
};

export function usePrefersStill(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches, () => false);
}
