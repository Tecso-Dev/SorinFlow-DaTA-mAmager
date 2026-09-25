"use client";

// The ONE place that decides which logo concept the app wears. The panel
// shell, the login page, the landing page, the icons and the OG image all
// render `LogoMark` / `Logo` from here; switching concept is this one line.
// The concepts themselves (and the gallery at /brand-preview) are in logos.tsx.

import { CONCEPTS, type ConceptKey, type LockupProps, type MarkProps } from "./logos";

/** الف = "house", ب = "ribbon", ج = "towers". The owner picks; "house" for now. */
export const CURRENT_CONCEPT: ConceptKey = "house";

export function LogoMark(props: MarkProps) {
  const { Mark } = CONCEPTS[CURRENT_CONCEPT];
  return <Mark {...props} />;
}

export function Logo(props: LockupProps) {
  const { Lockup } = CONCEPTS[CURRENT_CONCEPT];
  return <Lockup {...props} />;
}
