import { Suspense } from "react";
import { ListSkeleton } from "@/components/panel/kit";
import { SmsView } from "@/components/sms/sms-view";

export const metadata = { title: "پیامک" };

export default function Page() {
  return (
    <Suspense fallback={<ListSkeleton rows={8} />}>
      <SmsView />
    </Suspense>
  );
}
