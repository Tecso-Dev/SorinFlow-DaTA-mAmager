"use client";

import { UserCircle } from "lucide-react";
import { ErrorNote, ListSkeleton, PageHeader } from "@/components/panel/kit";
import { IsoIDCard } from "@/components/panel/motion3d";
import { Reveal } from "@/components/viz";
import { useSession } from "@/lib/session";
import { HeroCard } from "./hero-card";
import { DetailsForm } from "./details-form";
import { TelegramCard } from "./telegram-card";
import { ContactCard } from "./contact-card";
import { SecurityCard } from "./security-card";

export function ProfilePage() {
  const session = useSession();

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 items-center gap-4 lg:grid-cols-[minmax(0,1fr)_160px]">
        <PageHeader icon={UserCircle} title="پروفایل من" hint="اطلاعات حساب، امنیت و اتصال‌ها" />
        <Reveal delay={0.1} className="hidden lg:block">
          <IsoIDCard />
        </Reveal>
      </div>

      {session.isPending ? (
        <ListSkeleton rows={4} />
      ) : session.isError ? (
        <ErrorNote error={session.error} />
      ) : (
        <>
          <HeroCard me={session.data.user} />
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <div className="flex flex-col gap-5">
              <DetailsForm me={session.data.user} />
              <TelegramCard />
            </div>
            <div className="flex flex-col gap-5">
              <ContactCard me={session.data.user} />
              <SecurityCard me={session.data.user} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
