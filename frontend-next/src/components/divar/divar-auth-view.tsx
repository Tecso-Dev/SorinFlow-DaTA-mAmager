"use client";

import { KeyRound } from "lucide-react";
import { PageHeader } from "@/components/panel/kit";
import { IsoPhone } from "@/components/panel/motion3d";
import { Reveal } from "@/components/viz";
import { useSession } from "@/lib/session";
import { ImportCard } from "./import-card";
import { LoginCard } from "./login-card";
import { RegistryCard } from "./registry-card";
import { SavedSessionsCard } from "./saved-sessions-card";

export function DivarAuthView() {
  const user = useSession().data?.user;
  // The registry card is root-only in two places at once: it is not
  // rendered at all for anyone else (not just hidden by CSS), and it never
  // even mounts — so its GET /auth/registry is never fired for a non-root
  // session.
  const isRoot = user?.role === "root";

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={KeyRound}
        title="احراز هویت دیوار"
        hint="ورود، نشست‌های ذخیره‌شده و مالکیت شماره‌های دیوار"
      />
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_180px]">
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <LoginCard />
          <ImportCard />
        </div>
        <Reveal delay={0.1} className="hidden lg:block">
          <IsoPhone />
        </Reveal>
      </div>
      <Reveal delay={0.05}><SavedSessionsCard /></Reveal>
      {isRoot && <Reveal delay={0.1}><RegistryCard /></Reveal>}
    </div>
  );
}
