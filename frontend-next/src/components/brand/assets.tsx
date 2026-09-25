"use client";

// Fixed-size art for the generated brand files (OG image, icons). Colours are
// the «شب نیلی» page colours spelled out, because these are pictures of a
// dark card, not themed UI.

import { LogoMark } from "./logo";

export function OgCard({
  brandName, brandNameLatin, tagline,
}: { brandName: string; brandNameLatin: string; tagline: string }) {
  return (
    <div
      data-asset="og"
      className="relative flex h-[630px] w-[1200px] items-center justify-between overflow-hidden bg-[#05050a] px-24 text-[#eef1f8]"
    >
      <div className="absolute -top-40 -start-40 size-[640px] rounded-full bg-indigo-600/35 blur-[120px]" />
      <div className="absolute -bottom-52 end-40 size-[560px] rounded-full bg-violet-600/30 blur-[120px]" />
      <div className="absolute top-20 end-[420px] size-[260px] rounded-full bg-cyan-400/15 blur-[90px]" />
      <div className="relative flex max-w-[640px] flex-col gap-6">
        <div className="text-[104px] leading-[1.1] font-black tracking-tight">{brandName}</div>
        <div className="text-[40px] leading-snug font-bold text-[#c7d2fe]">{tagline}</div>
        <div className="mt-4 flex items-center gap-4 text-[26px] text-[#8f9ab0]" dir="ltr">
          <span className="size-2.5 rounded-full bg-cyan-400" />
          <span className="font-bold text-[#a5b4fc]">{brandNameLatin}</span>
        </div>
      </div>
      <div className="relative drop-shadow-[0_40px_60px_rgb(99_102_241/0.55)]">
        <LogoMark size={400} />
      </div>
    </div>
  );
}

export function IconArt({ kind }: { kind: "favicon" | "apple" | "maskable" }) {
  if (kind === "favicon") {
    return (
      <div data-asset="icon" className="grid size-[512px] place-items-center">
        <LogoMark size={512} />
      </div>
    );
  }
  // apple: iOS rounds the corners itself; maskable: Android crops to a circle
  // or squircle, so the art stays inside the middle 80%.
  return (
    <div
      data-asset="icon"
      className="grid size-[512px] place-items-center bg-[radial-gradient(circle_at_30%_25%,#1c1b3a,#05050a_70%)]"
    >
      <LogoMark size={kind === "apple" ? 380 : 320} />
    </div>
  );
}
