import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { MonitoringView } from "@/components/monitoring/monitoring-view";

export const metadata = { title: "پایش سامانه" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={8} />}>
      <MonitoringView />
    </Suspense>
  );
}
