"use client";

import { UserCog } from "lucide-react";
import { Empty, ErrorNote, PageHeader } from "@/components/panel/kit";
import { can, useSession } from "@/lib/session";
import { TicketsCard } from "./tickets-card";
import { MaintenanceCard } from "./maintenance-card";
import { BackupCard } from "./backup-card";
import { DrCard } from "./dr-card";
import { TeamTable } from "./team-table";

export function UsersPage() {
  const session = useSession();
  const user = session.data?.user;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={UserCog} title="کاربران و بکاپ" hint="تیم، تعمیر سایت، بکاپ و بازیابی" />

      {session.isPending ? (
        <div className="h-40 animate-pulse rounded-2xl bg-muted/40" />
      ) : session.isError ? (
        <ErrorNote error={session.error} />
      ) : !can(user, { roles: ["root", "super_admin"] }) ? (
        <Empty>این بخش فقط برای root و مدیر ارشد است.</Empty>
      ) : (
        <>
          <TicketsCard />
          <MaintenanceCard />
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <BackupCard />
            <DrCard />
          </div>
          <TeamTable me={user!} />
        </>
      )}
    </div>
  );
}
