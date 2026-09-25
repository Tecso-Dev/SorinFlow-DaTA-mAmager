import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { LeadsView } from "@/components/crm/leads/leads-view";

export const metadata = { title: "لیدها" };

// the filters live in the URL (useSearchParams), so the view renders on the client
export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={8} />}>
      <LeadsView />
    </Suspense>
  );
}
