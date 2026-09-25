import { Hammer } from "lucide-react";
import { notFound } from "next/navigation";
import { NAV_ITEMS } from "@/components/panel/nav";
import { faNum } from "@/lib/format";

// Sections not rebuilt yet say so and point at the current panel, which keeps
// working until every section is here.
export default async function SectionComing(props: PageProps<"/panel/[...section]">) {
  const { section } = await props.params;
  const item = NAV_ITEMS.find((i) => i.href === `/panel/${section.join("/")}`);
  if (!item) notFound();
  return (
    <div className="mx-auto mt-16 flex max-w-md flex-col items-center text-center">
      <div className="relative mb-5 grid size-[62px] place-items-center rounded-full bg-linear-to-br from-indigo-500 to-violet-600 shadow-[0_0_40px_-6px_rgb(99_102_241/0.8)]">
        <div className="absolute inset-[3px] rounded-full bg-card" />
        <Hammer className="relative size-6 text-primary" />
      </div>
      <h1 className="text-xl font-black">{item.label}</h1>
      <p className="mt-2 text-sm leading-7 text-muted-foreground">
        این بخش در مرحلهٔ {faNum(item.step)} به پنل تازه می‌آید. تا آن موقع از پنل فعلی استفاده کنید.
      </p>
      <a href={`/dashboard/#/${item.legacy}`} className="mt-5 text-sm font-semibold text-primary hover:underline">
        باز کردن در پنل فعلی
      </a>
    </div>
  );
}
