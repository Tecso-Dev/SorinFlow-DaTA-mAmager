# The new frontend (`frontend-next/`)

Phase 4 rebuilds the panel, the customer portal and the landing page as one Next.js 16 app.
The old panel (`frontend/`) keeps working at `/dashboard` until every section is here.

## Stack
- Next.js 16 App Router, TypeScript, React 19. Read `frontend-next/AGENTS.md`: this Next.js has
  breaking changes; its docs ship in `node_modules/next/dist/docs/`. Middleware is `src/proxy.ts`.
- Tailwind CSS 4 + shadcn/ui (`components.json`: style radix-nova, `rtl: true`). Add components
  with `npx shadcn@4.21.0 add <name>`; they land in `src/components/ui/` and belong to us.
- Data: TanStack Query + `src/lib/api.ts` (`api<T>(path, {json, method})`, errors are `ApiError`
  with a Persian `message`). Session: `useSession()` and `can(user, {perm, roles})` in
  `src/lib/session.ts` — the only place that decides who sees what.
- Charts: Recharts through `src/components/ui/chart.tsx`. Motion: `motion/react`. 3D and motion
  building blocks: `src/components/viz.tsx` (Reveal, CountUp, Tilt, Skyline, Donut3D, ...).
- Formatting: `src/lib/format.ts` (`faNum`, `faPercent`, `toman`, `faDate` Jalali, `parseDigits`).

## Look: «شب نیلی»
- Tokens live only in `src/app/globals.css` (`:root` light, `.dark` dark). Use `bg-card`,
  `text-muted-foreground`, `border`, `bg-primary`, `text-success|warning|destructive|info`,
  `chart-1..5`, `--glow-1/2`, `--viz-low/high`. Never hard-code a colour that a token covers.
- Cards: `rounded-2xl border bg-card`, in dark a faint top gradient (`dark:bg-linear-to-b
  dark:from-white/[0.035] dark:to-white/[0.008]`). Primary buttons carry an indigo glow.
- Dialogs follow the OTP dialog of the old panel: a 62px gradient ring holding an icon, a radial
  glow, a column of full-width buttons. Never `window.prompt/confirm/alert`.
- Theme follows the device (next-themes, `storageKey="sf-theme"`), both themes must look right.

## Rules every page follows
- **Persian, RTL.** `<html dir="rtl">`. Use logical utilities (`ms-`, `me-`, `ps-`, `pe-`,
  `start-`, `end-`, `text-start`, `border-s/e`, `rounded-s/e`). Physical sides only where the
  geometry is physical (a sheet sliding from the right, a centred glow). `translateX` is physical:
  mirror it in RTL. Charts run right to left (`<XAxis reversed>`, `<YAxis orientation="right">`).
  Numbers, phone numbers and codes are typed in `dir="ltr"` inputs.
- **Responsive.** Every page works at 390px wide with no horizontal page scroll (wide tables and
  grids scroll inside their own card), at tablet width, and on desktop. Grids use `grid-cols-1`
  (minmax 0) as the base so a wide child cannot widen the page.
- **Motion and 3D everywhere, never in the way.** Each section has motion (reveal on view,
  count-up numbers, tilt on hover, spring dialogs) and at least one 3D element (an isometric
  illustration in its header or empty state, a 3D chart where the data fits). Everything respects
  `prefers-reduced-motion` (MotionConfig reducedMotion="user" is global) and must stay smooth on a
  mid-range phone: CSS/SVG 3D in the panel; WebGL only on the landing page (three.js from npm,
  loaded when idle, paused off-screen).
- **Strict CSP** (`src/proxy.ts`): scripts need the request nonce; no inline `<script>`, no inline
  event handler strings, no `<style>` elements and no `dangerouslySetInnerHTML` styles. Style
  *attributes* (`style={{...}}`) are allowed. A library that injects a `<style>` must take the
  nonce (`useNonce()` from `src/components/nonce.tsx`), otherwise do not use it. No script or
  stylesheet from a CDN; every dependency comes from npm through the lockfile.
- **Brand from settings.** Never spell the brand, office, domain or contact details in a page:
  server components call `getSiteConfig()` (`src/lib/site.ts`), client components get it as props.
- **Accessibility.** Real buttons and links, labels on inputs, `aria-label` on icon buttons,
  visible focus rings, colour is never the only signal.

## Checks before a commit
```bash
cd frontend-next && npm ci && npx next typegen && npx tsc --noEmit && npm run lint && npx next build
```
