import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { DivarAuthView } from "@/components/divar/divar-auth-view";

export const metadata = { title: "احراز هویت دیوار" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={6} />}>
      <DivarAuthView />
    </Suspense>
  );
}
