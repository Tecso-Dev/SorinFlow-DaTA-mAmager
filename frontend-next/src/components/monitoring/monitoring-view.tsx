"use client";

// پایش سامانه — «نمای کلی» (the old section, unchanged) plus the new
// monitoring contract's tabs from docs/MONITORING.md §8. Only root and
// super_admin see the new tabs; everyone with the monitoring permission
// sees نمای کلی.

import { Activity } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/panel/kit";
import { IsoAntenna } from "@/components/panel/motion3d";
import { Reveal } from "@/components/viz";
import { useSession, can } from "@/lib/session";
import { OverviewTab } from "./overview-tab";
import { AlertsTab, CicdTab, K8sTab, ServerTab, ServicesTab } from "./extra-tabs";

export function MonitoringView() {
  const user = useSession().data?.user;
  const isBoss = can(user, { roles: ["root", "super_admin"] });

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 items-center gap-4 lg:grid-cols-[minmax(0,1fr)_130px]">
        <PageHeader icon={Activity} title="پایش سامانه" hint="سلامت سرور، اتصال دیوار و صف‌های پس‌زمینه" />
        <Reveal delay={0.1} className="hidden lg:block">
          <IsoAntenna className="max-w-[108px]" />
        </Reveal>
      </div>

      {isBoss ? (
        <Tabs defaultValue="overview" className="gap-4">
          <TabsList className="w-full flex-wrap justify-start sm:w-fit">
            <TabsTrigger value="overview">نمای کلی</TabsTrigger>
            <TabsTrigger value="server">سرور</TabsTrigger>
            <TabsTrigger value="k8s">کوبرنتیز</TabsTrigger>
            <TabsTrigger value="services">سرویس‌ها</TabsTrigger>
            <TabsTrigger value="cicd">CI/CD</TabsTrigger>
            <TabsTrigger value="alerts">هشدارها</TabsTrigger>
          </TabsList>
          <TabsContent value="overview"><OverviewTab user={user} /></TabsContent>
          <TabsContent value="server"><ServerTab /></TabsContent>
          <TabsContent value="k8s"><K8sTab /></TabsContent>
          <TabsContent value="services"><ServicesTab /></TabsContent>
          <TabsContent value="cicd"><CicdTab /></TabsContent>
          <TabsContent value="alerts"><AlertsTab /></TabsContent>
        </Tabs>
      ) : (
        <OverviewTab user={user} />
      )}
    </div>
  );
}
