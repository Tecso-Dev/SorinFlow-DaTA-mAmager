import { redirect } from "next/navigation";

// the call queue is where an agent's day starts, as in the old panel
export default function CrmIndex() {
  redirect("/panel/crm/calls");
}
