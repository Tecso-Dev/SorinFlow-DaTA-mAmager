"use client";

// A listing in the side drawer (the old panel's viewProperty), opened from a
// match card, a price drop or a similar-properties row.

import { ExternalLink, Network, Share2, UserCheck } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ErrorNote, ListSkeleton } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import { ShareDialog } from "./dialogs";
import { MatchDialog, type MatchTarget } from "./match-dialog";
import { PropertyDetails } from "./property-details";
import { AgencyBadge, DupBadge, listingMoney, PhoneWithCopy, SerialBadge } from "./shared";
import { SideSheet } from "./side-sheet";
import type { PropertyDetail } from "./types";

export function PropertySheet({ id, onClose }: { id: number | null; onClose: () => void }) {
  const user = useSession().data?.user;
  const [match, setMatch] = useState<MatchTarget | null>(null);
  const [share, setShare] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["properties", id],
    queryFn: () => api<PropertyDetail>(`/properties/${id}`),
    enabled: id !== null,
    retry: false,
  });
  const p = q.data;
  return (
    <>
      <SideSheet
        open={id !== null}
        onOpenChange={(o) => !o && onClose()}
        title={p?.title ?? "جزئیات ملک"}
        header={
          p ? (
            <div className="grid gap-1.5 pe-2">
              <div className="flex flex-wrap items-center gap-1.5">
                <SerialBadge serial={p.serial_no} />
                <AgencyBadge p={p} />
                <DupBadge of={p.ai_duplicate_of} />
              </div>
              <h2 className="text-lg leading-7 font-black">{p.title}</h2>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="font-bold text-primary">{listingMoney(p)}</span>
                {p.phone_number && <PhoneWithCopy phone={p.phone_number} />}
              </div>
            </div>
          ) : undefined
        }
      >
        {q.isLoading ? (
          <ListSkeleton rows={6} />
        ) : q.isError ? (
          <ErrorNote error={q.error} />
        ) : p ? (
          <div className="grid gap-4">
            <div className="flex flex-wrap gap-2">
              {p.url && (
                <Button asChild variant="outline" size="sm">
                  <a href={p.url} target="_blank" rel="noopener noreferrer"><ExternalLink /> مشاهدهٔ آگهی</a>
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => setMatch({ kind: "property", id: p.id })}><Network /> ملک‌های مشابه</Button>
              <Button variant="outline" size="sm" onClick={() => setMatch({ kind: "customers", id: p.id })}><UserCheck /> متقاضیان هم‌خوان</Button>
              {can(user, { perm: "filing" }) && (
                <Button variant="outline" size="sm" onClick={() => setShare(p.id)}><Share2 /> ارسال برای مشتری</Button>
              )}
            </div>
            <PropertyDetails p={p} invalidate={[["properties", p.id]]} editable={can(user, { perm: "properties" })} />
          </div>
        ) : null}
      </SideSheet>
      {/* mounted only while open: the dialog can open a property sheet in turn */}
      {match && <MatchDialog target={match} onClose={() => setMatch(null)} />}
      <ShareDialog propertyId={share} onClose={() => setShare(null)} />
    </>
  );
}
