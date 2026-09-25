import { Building2, Handshake, Search } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";
import type { Metadata } from "next";
import { Skyline } from "@/components/viz";
import { AuthView } from "@/components/portal/auth-view";
import { BACKEND, getSiteConfig } from "@/lib/site";

export async function generateMetadata(): Promise<Metadata> {
  const site = await getSiteConfig();
  return {
    title: "پورتال مشتریان",
    description: site.seoDescription || `درخواست ملک مورد نظرتان را برای ${site.brandName} ثبت کنید و نتیجه را پیگیری کنید.`,
  };
}

// Decorative only, same as the panel's login page.
const SKYLINE = [
  { name: "الف", count: 84, ppm: 142, delta: 0 },
  { name: "ب", count: 71, ppm: 96, delta: 0 },
  { name: "ج", count: 58, ppm: 168, delta: 0 },
  { name: "د", count: 52, ppm: 155, delta: 0 },
  { name: "ه", count: 47, ppm: 74, delta: 0 },
  { name: "و", count: 33, ppm: 181, delta: 0 },
];

const POINTS = [
  { icon: Search, text: "خواستهٔ خود را بنویسید: شهر، محله، بودجه و متراژ." },
  { icon: Building2, text: "کارشناسان ما ملک‌های مناسب را از میان آگهی‌ها پیدا می‌کنند." },
  { icon: Handshake, text: "همهٔ درخواست‌های خود و پاسخشان را همین‌جا پیگیری کنید." },
];

async function authEnabled(): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND}/api/public/auth/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1500),
    });
    if (!res.ok) return true; // a transient error must not lock visitors out
    const data = (await res.json()) as { enabled?: boolean };
    return !!data.enabled;
  } catch {
    return true;
  }
}

function ComingSoon({ brandName }: { brandName: string }) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-5 text-center">
      <div className="grid size-14 place-items-center rounded-2xl bg-linear-to-br from-indigo-500 to-violet-600 text-lg font-black text-white shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]">
        {brandName.slice(0, 1)}
      </div>
      <h1 className="text-xl font-black">پورتال مشتریان به‌زودی فعال می‌شود</h1>
      <p className="max-w-sm text-sm text-muted-foreground">
        ثبت‌نام و پیگیری درخواست از طریق این صفحه هنوز باز نشده است.
      </p>
      <Link href="/panel/login" className="text-sm text-primary hover:underline">
        کاربر پنل هستید؟ ورود به پنل مدیریت
      </Link>
    </main>
  );
}

export default async function PortalPage() {
  const [site, enabled] = await Promise.all([getSiteConfig(), authEnabled()]);
  if (!enabled) return <ComingSoon brandName={site.brandName} />;

  return (
    <div className="grid min-h-dvh lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <main className="relative flex flex-col items-center justify-center overflow-hidden px-5 py-10">
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <div className="absolute -top-32 left-1/2 size-[480px] -translate-x-1/2 rounded-full bg-(--glow-1) blur-3xl" />
        </div>
        <div className="relative flex w-full flex-col items-center">
          <div className="mb-10 flex items-center gap-2.5">
            <div
              aria-hidden
              className="grid size-10 place-items-center rounded-xl bg-linear-to-br from-indigo-500 to-violet-600 text-base font-black text-white shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]"
            >
              {site.brandNameLatin.slice(0, 1)}
            </div>
            <div className="leading-tight">
              <div className="text-lg font-extrabold">{site.brandName}</div>
              <div className="text-xs text-muted-foreground">پورتال مشتریان</div>
            </div>
          </div>
          <Suspense>
            <AuthView />
          </Suspense>
        </div>
      </main>

      <aside className="relative hidden overflow-hidden bg-[#07070d] p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <div className="absolute -top-24 -start-24 size-[520px] rounded-full bg-indigo-600/30 blur-3xl" />
          <div className="absolute -bottom-32 end-0 size-[460px] rounded-full bg-violet-600/25 blur-3xl" />
        </div>
        <div className="relative max-w-md">
          <h2 className="text-3xl leading-[1.5] font-black">دنبال ملک بعدی‌تان بگردید؛ بقیه‌اش با ما.</h2>
          <ul className="mt-8 flex flex-col gap-4 text-sm text-white/80">
            {POINTS.map((p) => (
              <li key={p.text} className="flex items-center gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-white/10 ring-1 ring-white/15">
                  <p.icon className="size-4" />
                </span>
                {p.text}
              </li>
            ))}
          </ul>
        </div>
        <div
          aria-hidden
          className="relative mx-auto w-full max-w-lg [--border:rgb(255_255_255/0.14)] [--foreground:white] [--muted:rgb(255_255_255/0.06)] [--viz-high:#f472b6] [--viz-low:#818cf8]"
        >
          <Skyline data={SKYLINE} legend={false} />
        </div>
      </aside>
    </div>
  );
}
