import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { AiView } from "@/components/ai/ai-view";

export const metadata = { title: "هوش مصنوعی" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={8} />}>
      <AiView />
    </Suspense>
  );
}
