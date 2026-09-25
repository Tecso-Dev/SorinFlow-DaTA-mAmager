"use client";

// کمد و زونکن — the cabinet/binder tree on one side, the card grid of
// whatever is open on the other. A "file" is a Property row; nothing here
// duplicates property data, only where it lives and how it is marked
// (app/api/routes/filing.py).

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronDown, FolderArchive, Inbox, Lock, Menu, Pin } from "lucide-react";
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Empty, ErrorNote, ListSkeleton, PageHeader, Section } from "@/components/panel/kit";
import { Reveal, Tilt, CountUp } from "@/components/viz";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { qs } from "@/lib/crm";
import { faNum } from "@/lib/format";
import { FilingFilters } from "./filters";
import { FilingTree } from "./tree";
import { FileCard } from "./file-card";
import { BulkBar } from "./bulk-bar";
import { MovePicker } from "./move-picker";
import { FileEditDialog } from "./file-edit-dialog";
import { ShareDialog } from "./share-dialog";
import { allBinders, EMPTY_ADVANCED, type AdvancedFilters, type Cabinet, type FileBrief, type Overview } from "./types";

export type Scope = { kind: "unfiled" } | { kind: "binder"; id: number };

const PAGE = 60;

export function FilingPage() {
  const qc = useQueryClient();

  const [scope, setScope] = useState<Scope>({ kind: "unfiled" });
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [archived, setArchived] = useState(false);
  const [advanced, setAdvanced] = useState<AdvancedFilters>(EMPTY_ADVANCED);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editId, setEditId] = useState<number | null>(null);
  const [editSeq, setEditSeq] = useState(0);
  const [shareId, setShareId] = useState<number | null>(null);
  const [moveOpen, setMoveOpen] = useState<false | "bulk" | number>(false);
  const [treeSheet, setTreeSheet] = useState(false);

  function resetScope(next: Scope) {
    setScope(next);
    setSelected(new Set());
  }
  function resetAnd<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v);
      setSelected(new Set());
    };
  }

  const cabinetsQuery = useQuery({
    queryKey: ["crm", "filing", "cabinets"],
    queryFn: () => api<{ items: Cabinet[]; total: number }>("/filing/cabinets"),
  });
  const overviewQuery = useQuery({
    queryKey: ["crm", "filing", "overview"],
    queryFn: () => api<Overview>("/filing/overview"),
  });
  const tagsQuery = useQuery({
    queryKey: ["crm", "filing", "tags"],
    queryFn: () => api<{ items: { name: string; count: number }[]; total: number }>("/filing/tags"),
  });

  const cabinets = useMemo(() => cabinetsQuery.data?.items ?? [], [cabinetsQuery.data]);
  const narrowed = !!(search || tag || Object.values(advanced).some((v) => (typeof v === "boolean" ? v : v !== "")));

  const filesQuery = useInfiniteQuery({
    queryKey: ["crm", "filing", "files", scope, search, tag, archived, advanced],
    queryFn: async ({ pageParam }) => {
      const params: Record<string, string | number | boolean | undefined> = {
        limit: PAGE, offset: pageParam as number, archived,
        search: search || undefined, tag: tag || undefined,
        price_min: advanced.price_min || undefined, price_max: advanced.price_max || undefined,
        area_min: advanced.area_min || undefined, area_max: advanced.area_max || undefined,
        rooms_min: advanced.rooms_min || undefined, district: advanced.district || undefined,
        property_type: advanced.property_type || undefined, listing_type: advanced.listing_type || undefined,
        has_elevator: advanced.has_elevator || undefined, has_parking: advanced.has_parking || undefined,
        has_storage: advanced.has_storage || undefined,
      };
      if (scope.kind === "binder") params.binder_id = scope.id;
      else if (!narrowed) params.unfiled = true;
      return api<{ items: FileBrief[]; total: number }>(`/filing/files${qs(params)}`);
    },
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const shown = pages.reduce((n, p) => n + p.items.length, 0);
      return shown < last.total ? shown : undefined;
    },
  });

  const items = useMemo(() => filesQuery.data?.pages.flatMap((p) => p.items) ?? [], [filesQuery.data]);
  const total = filesQuery.data?.pages.at(-1)?.total ?? 0;
  const bindersFlat = useMemo(() => allBinders(cabinets), [cabinets]);
  const activeBinder = scope.kind === "binder" ? bindersFlat.find((b) => b.id === scope.id) : null;

  const gridTitle = archived
    ? "فایل‌های بایگانی‌شده"
    : scope.kind === "binder"
      ? `زونکن: ${activeBinder?.name ?? "…"}`
      : narrowed
        ? "نتیجهٔ جستجو در همهٔ زونکن‌ها"
        : "فایل‌های بدون زونکن";

  function invalidateAll() {
    qc.invalidateQueries({ queryKey: ["crm", "filing"] });
  }

  function toggleSelect(id: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function bulk(action: string, extra?: Record<string, unknown>) {
    if (!selected.size) return;
    try {
      const r = await api<{ updated: number; skipped: number }>("/filing/files/bulk", {
        json: { ids: [...selected], action, ...extra },
      });
      toast.success(`روی ${faNum(r.updated)} فایل انجام شد`, r.skipped ? `${faNum(r.skipped)} فایل قابل دسترس نبود` : undefined);
      setSelected(new Set());
      invalidateAll();
    } catch (e) {
      toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  async function quickAction(id: number, action: "pin" | "unpin" | "archive" | "unarchive") {
    try {
      await api("/filing/files/bulk", { json: { ids: [id], action } });
      invalidateAll();
    } catch (e) {
      toast.error("انجام نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  async function moveOne(id: number, binderId: number | null) {
    try {
      await api("/filing/files/bulk", { json: { ids: [id], action: "move", binder_id: binderId } });
      toast.success("جابه‌جا شد");
      invalidateAll();
    } catch (e) {
      toast.error("جابه‌جا نشد", e instanceof ApiError ? e.message : undefined);
    }
  }

  const treeNode = (
    <FilingTree
      cabinets={cabinets}
      scope={scope}
      onScope={(s) => {
        resetScope(s);
        setTreeSheet(false);
      }}
      unfiledCount={overviewQuery.data?.unfiled}
      onChanged={invalidateAll}
    />
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        icon={FolderArchive}
        title="کمد و زونکن"
        hint="فایل‌های ملکی، دسته‌بندی‌شده در کمد و زونکن، آمادهٔ اشتراک‌گذاری با مشتری"
        actions={
          <Sheet open={treeSheet} onOpenChange={setTreeSheet}>
            <SheetTrigger asChild>
              <Button variant="outline" className="gap-1.5 lg:hidden">
                <Menu className="size-4" /> کمدها
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-80 overflow-y-auto p-0">
              <SheetHeader>
                <SheetTitle>کمد و زونکن</SheetTitle>
              </SheetHeader>
              <div className="px-3 pb-4">{treeNode}</div>
            </SheetContent>
          </Sheet>
        }
      />

      <Reveal>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {([
            ["filed", "بایگانی‌شده در زونکن", Inbox],
            ["unfiled", "بدون زونکن", FolderArchive],
            ["pinned", "سنجاق‌شده", Pin],
            ["archived", "بایگانی", Archive],
            ["private", "خصوصی", Lock],
          ] as const).map(([key, label, Icon]) => (
            <Tilt key={key} max={4}>
              <div className="flex flex-col gap-1 rounded-2xl border bg-card p-3 dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]">
                <Icon className="size-4 text-muted-foreground" />
                <span className="text-lg font-black tabular">
                  {overviewQuery.data ? <CountUp value={overviewQuery.data[key]} /> : "—"}
                </span>
                <span className="text-[11px] text-muted-foreground">{label}</span>
              </div>
            </Tilt>
          ))}
        </div>
      </Reveal>

      <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
        <Section title="کمدها" className="hidden lg:flex" bodyClassName="max-h-[70vh] overflow-y-auto p-2">
          {cabinetsQuery.isLoading ? <ListSkeleton /> : cabinetsQuery.isError ? <ErrorNote error={cabinetsQuery.error} /> : treeNode}
        </Section>

        <Section
          title={gridTitle}
          hint={`${faNum(total)} فایل`}
          bodyClassName="flex flex-col gap-4"
        >
          <FilingFilters
            search={search} onSearch={resetAnd(setSearch)}
            tags={tagsQuery.data?.items ?? []} tag={tag} onTag={resetAnd(setTag)}
            archived={archived} onArchived={resetAnd(setArchived)}
            advanced={advanced} onAdvanced={resetAnd(setAdvanced)}
          />

          {filesQuery.isLoading ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              <ListSkeleton rows={6} />
            </div>
          ) : filesQuery.isError ? (
            <ErrorNote error={filesQuery.error} />
          ) : !items.length ? (
            <Empty icon={FolderArchive}>{narrowed ? "چیزی با این فیلتر پیدا نشد" : "فایلی اینجا نیست"}</Empty>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {items.map((f) => {
                  const where = f.binder_id && f.binder_id !== (scope.kind === "binder" ? scope.id : -1)
                    ? bindersFlat.find((b) => b.id === f.binder_id) : null;
                  return (
                    <FileCard
                      key={f.id}
                      file={f}
                      selected={selected.has(f.id)}
                      onToggle={() => toggleSelect(f.id)}
                      whereBinder={where}
                      onEdit={() => { setEditId(f.id); setEditSeq((s) => s + 1); }}
                      onMove={() => setMoveOpen(f.id)}
                      onShare={() => setShareId(f.id)}
                      onQuickAction={(a) => quickAction(f.id, a)}
                    />
                  );
                })}
              </div>
              {filesQuery.hasNextPage && (
                <Button
                  variant="outline" className="mx-auto gap-1.5" disabled={filesQuery.isFetchingNextPage}
                  onClick={() => filesQuery.fetchNextPage()}
                >
                  <ChevronDown className="size-4" />
                  {filesQuery.isFetchingNextPage ? "در حال بارگیری…" : `نمایش ${faNum(Math.min(PAGE, total - items.length))} فایل بعدی`}
                </Button>
              )}
            </>
          )}
        </Section>
      </div>

      <BulkBar
        count={selected.size}
        onAction={(a) => bulk(a)}
        onMove={() => setMoveOpen("bulk")}
        onTag={(tags, remove) => bulk(remove ? "untag" : "tag", { tags })}
        onClear={() => setSelected(new Set())}
      />

      <MovePicker
        open={!!moveOpen}
        onOpenChange={(o) => !o && setMoveOpen(false)}
        cabinets={cabinets}
        onPick={(binderId) => {
          if (moveOpen === "bulk") bulk("move", { binder_id: binderId });
          else if (typeof moveOpen === "number") moveOne(moveOpen, binderId);
          setMoveOpen(false);
        }}
      />

      <FileEditDialog
        key={editSeq}
        open={!!editId}
        onOpenChange={(o) => !o && setEditId(null)}
        fileId={editId}
        cabinets={cabinets}
        onSaved={invalidateAll}
      />
      <ShareDialog open={!!shareId} onOpenChange={(o) => !o && setShareId(null)} fileId={shareId} />
    </div>
  );
}
