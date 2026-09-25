import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { EmailView } from "@/components/email/email-view";

export const metadata = { title: "ایمیل" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={8} />}>
      <EmailView />
    </Suspense>
  );
}
