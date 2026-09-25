import { CrmFrame } from "@/components/crm/crm-frame";

export const metadata = { title: "CRM و لیدها" };

export default function CrmLayout({ children }: LayoutProps<"/panel/crm">) {
  return <CrmFrame>{children}</CrmFrame>;
}
