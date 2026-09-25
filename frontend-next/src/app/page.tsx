import { redirect } from "next/navigation";

// ponytail: the landing page is milestone 9; until then / shows the proposals.
export default function Home() {
  redirect("/preview/a");
}
