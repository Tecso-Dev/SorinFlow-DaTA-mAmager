"use client";

// «هوش تصویری» — two tabs: تحلیل تصویری و قیمت (visual/valuation, gated on
// the `properties` permission at the router level) and قیف و عملکرد
// (pipeline, gated on `crm`). The section itself sits under the `crm`
// permission in the sidebar (nav.ts), matching the old panel's
// SECTION_PERMISSION.insights = 'crm' quirk — see the migration inventory.

import { Eye, Funnel as FunnelIcon, ScanEye } from "lucide-react";
import { PageHeader } from "@/components/panel/kit";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PipelineTab } from "./pipeline-tab";
import { VisualTab } from "./visual-tab";

export function InsightsView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={ScanEye} title="هوش تصویری" hint="آنچه از قیمت‌ها و عکس‌های واقعی اندازه‌گیری می‌شود، و قیف فروش" />

      <Tabs defaultValue="visual">
        <TabsList>
          <TabsTrigger value="visual"><Eye /> تحلیل تصویری و قیمت</TabsTrigger>
          <TabsTrigger value="pipeline"><FunnelIcon /> قیف و عملکرد</TabsTrigger>
        </TabsList>
        <TabsContent value="visual" className="mt-4"><VisualTab /></TabsContent>
        <TabsContent value="pipeline" className="mt-4"><PipelineTab /></TabsContent>
      </Tabs>
    </div>
  );
}
