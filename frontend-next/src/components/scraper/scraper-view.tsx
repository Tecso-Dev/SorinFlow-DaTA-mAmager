"use client";

// «اسکرپر»: everything for pulling listings out of Divar — a new run (from
// scratch, from a pasted link, or as a daily schedule), a single listing,
// and the table of every run with its log, its skipped ads and its
// controls. The OTP dialog itself is mounted once in the app shell by a
// different part of the panel; this section only surfaces what a job's
// cancel answers about it (`otp_cleared`).

import { Bot } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "@/components/panel/kit";
import { Reveal } from "@/components/viz";
import { api } from "@/lib/api";
import { JobsTable } from "./jobs-table";
import { NewScrapeForm } from "./new-scrape-form";
import { SchedulesCard } from "./schedules-card";
import { SingleScrapeCard } from "./single-scrape-card";
import type { Category } from "./types";

export function ScraperView() {
  const categories = useQuery({
    queryKey: ["scraper", "categories"],
    queryFn: () => api<Category[]>("/scraper/categories"),
    staleTime: 10 * 60_000,
  });

  return (
    <div className="flex flex-col gap-5">
      <PageHeader icon={Bot} title="اسکرپر" hint="آگهی‌های دیوار را طبق فیلترهای شما جمع‌آوری می‌کند" />

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <div className="flex flex-col gap-5">
          <Reveal><NewScrapeForm /></Reveal>
          <Reveal delay={0.05}><SingleScrapeCard /></Reveal>
        </div>
        <div className="flex flex-col gap-5">
          <Reveal delay={0.05}><SchedulesCard onRanNow={() => {}} /></Reveal>
          <Reveal delay={0.1}><JobsTable categories={categories.data ?? []} /></Reveal>
        </div>
      </div>
    </div>
  );
}
