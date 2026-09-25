"use client";

import { Hammer } from "lucide-react";
import { Empty } from "@/components/panel/kit";
import { crmTab } from "./tabs";

/** A CRM tab not rebuilt yet: says so and opens it in the current panel. */
export function ComingTab({ slug }: { slug: string }) {
  const tab = crmTab(slug);
  return (
    <Empty icon={Hammer} action={<a href="/dashboard/#/crm" className="text-sm font-semibold text-primary hover:underline">باز کردن در پنل فعلی</a>}>
      «{tab?.label}» به‌زودی در پنل تازه می‌آید.
    </Empty>
  );
}
