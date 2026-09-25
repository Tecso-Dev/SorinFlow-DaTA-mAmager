import { Suspense } from "react";
import { ForwarderView } from "@/components/forwarder/forwarder-view";
import { ListSkeleton } from "@/components/panel/kit";

export const metadata = { title: "فرستندهٔ پیامک" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={4} />}>
      <ForwarderView />
    </Suspense>
  );
}
