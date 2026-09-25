import { redirect } from "next/navigation";

// ponytail: the landing page is step 9; until then / opens the panel.
export default function Home() {
  redirect("/panel");
}
