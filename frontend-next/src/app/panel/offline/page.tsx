import type { Metadata } from "next";
import { OfflinePage } from "@/components/pwa/offline-page";

export const metadata: Metadata = { title: "بدون اینترنت" };

export default function PanelOffline() {
  return <OfflinePage homeLabel="بازگشت به داشبورد" homeHref="/panel" />;
}
