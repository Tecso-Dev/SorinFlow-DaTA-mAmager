"use client";

import { KeyRound } from "lucide-react";
import { PageHeader } from "@/components/panel/kit";
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
      <div className="grid gap-5 lg:grid-cols-2">
        <LoginCard />
        <ImportCard />
      </div>
      <SavedSessionsCard />
      {isRoot && <RegistryCard />}
    </div>
  );
}
