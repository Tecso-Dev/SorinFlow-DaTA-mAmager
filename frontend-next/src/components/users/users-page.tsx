"use client";

import { UserCog } from "lucide-react";
import { Empty, ErrorNote, ListSkeleton, PageHeader } from "@/components/panel/kit";
import { IsoAlert, IsoIDCard } from "@/components/panel/motion3d";
import { Lottie } from "@/components/ui/lottie";
import { Reveal } from "@/components/viz";
import { can, useSession } from "@/lib/session";
import shieldLottie from "@/lotties/shield.json";
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
      <div className="grid grid-cols-1 items-center gap-4 lg:grid-cols-[minmax(0,1fr)_160px]">
        <PageHeader icon={UserCog} title="کاربران و بکاپ" hint="تیم، تعمیر سایت، بکاپ و بازیابی" />
        <Reveal delay={0.1} className="hidden lg:block">
          <IsoIDCard className="max-w-[130px]" />
        </Reveal>
      </div>

      {session.isPending ? (
        <ListSkeleton rows={4} />
      ) : session.isError ? (
        <ErrorNote error={session.error} illustration={<IsoAlert />} />
      ) : !can(user, { roles: ["root", "super_admin"] }) ? (
        <Empty illustration={<Lottie animationData={shieldLottie} className="max-w-[100px]" />}>این بخش فقط برای root و مدیر ارشد است.</Empty>
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
