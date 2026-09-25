"use client";

// The signed-in visitor's own page: who they are + logout, the property-need
// form, «درخواست‌های من» and the panel-access ticket — the three cards of the
// old frontend/portal.html's #portal-view, rebuilt on the shared session
// cookie and TanStack Query instead of a hand-rolled fetch/DOM dance.

import { ExternalLink, Inbox, LogOut, Moon, Sun, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Reveal, Tilt } from "@/components/viz";
import {
  Empty, ErrorNote, Field, ListSkeleton, NativeSelect, Section, ToneBadge, useConfirm,
} from "@/components/panel/kit";
import { toast } from "@/components/toaster";
import { api, ApiError, onApiError } from "@/lib/api";
import { parseDigits, faDate } from "@/lib/format";
import type { Tone } from "@/lib/crm";
import { SESSION_KEY, useSession } from "@/lib/session";

type Ticket = {
  id: number;
  status: "pending" | "approved" | "rejected";
  message: string | null;
  decision_note: string | null;
  created_at: string;
};

type Me = {
  full_name: string | null;
  phone: string | null;
  email: string | null;
  requests_count: number;
  ticket: Ticket | null;
};

type PropertyRequest = {
  id: number;
  deal_type: string;
  property_kind: string | null;
  city: string | null;
  districts: string | null;
  budget_min: number | null;
  budget_max: number | null;
  deposit_max: number | null;
  rent_max: number | null;
  area_min: number | null;
  area_max: number | null;
  description: string | null;
  admin_note: string | null;
  status: string;
  created_at: string;
};

const STATUS: Record<string, { label: string; tone: Tone }> = {
  new: { label: "ثبت شده", tone: "info" },
  in_review: { label: "در حال بررسی", tone: "warning" },
  matched: { label: "مورد پیدا شد", tone: "success" },
  contacted: { label: "تماس گرفته شد", tone: "success" },
  closed: { label: "بسته شده", tone: "neutral" },
};
const DEAL_LABEL: Record<string, string> = { buy: "خرید", rent: "اجاره" };
const KIND_OPTIONS = [
  { value: "", label: "فرقی ندارد" },
  { value: "apartment", label: "آپارتمان" },
  { value: "villa", label: "ویلایی / خانه" },
  { value: "land", label: "زمین / کلنگی" },
  { value: "office", label: "دفتر کار" },
  { value: "store", label: "مغازه" },
];

function numOrNull(v: string): number | null {
  const digits = parseDigits(v).replace(/\D/g, "");
  return digits ? parseInt(digits, 10) : null;
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="تغییر تم روشن و تیره"
      onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
    >
      <Sun className="size-[18px] dark:hidden" />
      <Moon className="hidden size-[18px] dark:block" />
    </Button>
  );
}

function RequestForm({ site }: { site: { brandName: string } }) {
  const qc = useQueryClient();
  const [deal, setDeal] = useState<"buy" | "rent">("buy");
  const [kind, setKind] = useState("");
  const [city, setCity] = useState("");
  const [districts, setDistricts] = useState("");
  const [budgetMin, setBudgetMin] = useState("");
  const [budgetMax, setBudgetMax] = useState("");
  const [depositMax, setDepositMax] = useState("");
  const [rentMax, setRentMax] = useState("");
  const [areaMin, setAreaMin] = useState("");
  const [areaMax, setAreaMax] = useState("");
  const [roomsMin, setRoomsMin] = useState("");
  const [yearMin, setYearMin] = useState("");
  const [elevator, setElevator] = useState(false);
  const [parking, setParking] = useState(false);
  const [storage, setStorage] = useState(false);
  const [desc, setDesc] = useState("");

  function reset() {
    setKind("");
    setCity("");
    setDistricts("");
    setBudgetMin("");
    setBudgetMax("");
    setDepositMax("");
    setRentMax("");
    setAreaMin("");
    setAreaMax("");
    setRoomsMin("");
    setYearMin("");
    setElevator(false);
    setParking(false);
    setStorage(false);
    setDesc("");
  }

  const create = useMutation({
    mutationFn: () =>
      api<PropertyRequest>("/portal/requests", {
        json: {
          deal_type: deal,
          property_kind: kind || null,
          city: city.trim() || null,
          districts: districts.trim() || null,
          budget_min: deal === "buy" ? numOrNull(budgetMin) : null,
          budget_max: deal === "buy" ? numOrNull(budgetMax) : null,
          deposit_max: deal === "rent" ? numOrNull(depositMax) : null,
          rent_max: deal === "rent" ? numOrNull(rentMax) : null,
          area_min: numOrNull(areaMin),
          area_max: numOrNull(areaMax),
          rooms_min: numOrNull(roomsMin),
          year_built_min: numOrNull(yearMin),
          needs_elevator: elevator,
          needs_parking: parking,
          needs_storage: storage,
          description: desc.trim() || null,
        },
      }),
    onSuccess: () => {
      toast.success("درخواست شما ثبت شد", "کارشناسان ما بررسی می‌کنند.");
      reset();
      void qc.invalidateQueries({ queryKey: ["portal", "requests"] });
      void qc.invalidateQueries({ queryKey: ["portal", "me"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ثبت درخواست ناموفق بود"),
  });

  return (
    <Section title="دنبال چه ملکی هستید؟" hint={`هرچه دقیق‌تر بنویسید، کارشناسان ${site.brandName} سریع‌تر مورد مناسب را پیدا می‌کنند.`}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2"
      >
        <Field label="نوع معامله" htmlFor="rq-deal">
          <NativeSelect id="rq-deal" value={deal} onChange={(e) => setDeal(e.target.value as "buy" | "rent")}>
            <option value="buy">خرید</option>
            <option value="rent">اجاره</option>
          </NativeSelect>
        </Field>
        <Field label="نوع ملک" htmlFor="rq-kind">
          <NativeSelect id="rq-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="شهر" htmlFor="rq-city">
          <Input id="rq-city" placeholder="مثلاً ارومیه" value={city} onChange={(e) => setCity(e.target.value)} />
        </Field>
        <Field label="محله‌های مورد نظر" htmlFor="rq-districts">
          <Input id="rq-districts" placeholder="با ویرگول جدا کنید" value={districts} onChange={(e) => setDistricts(e.target.value)} />
        </Field>

        {deal === "buy" ? (
          <>
            <Field label="حداقل بودجه (تومان)" htmlFor="rq-bmin">
              <Input id="rq-bmin" dir="ltr" inputMode="numeric" value={budgetMin} onChange={(e) => setBudgetMin(e.target.value)} />
            </Field>
            <Field label="حداکثر بودجه (تومان)" htmlFor="rq-bmax">
              <Input id="rq-bmax" dir="ltr" inputMode="numeric" value={budgetMax} onChange={(e) => setBudgetMax(e.target.value)} />
            </Field>
          </>
        ) : (
          <>
            <Field label="حداکثر ودیعه (تومان)" htmlFor="rq-dep">
              <Input id="rq-dep" dir="ltr" inputMode="numeric" value={depositMax} onChange={(e) => setDepositMax(e.target.value)} />
            </Field>
            <Field label="حداکثر اجاره ماهانه (تومان)" htmlFor="rq-rent">
              <Input id="rq-rent" dir="ltr" inputMode="numeric" value={rentMax} onChange={(e) => setRentMax(e.target.value)} />
            </Field>
          </>
        )}

        <Field label="حداقل متراژ" htmlFor="rq-amin">
          <Input id="rq-amin" dir="ltr" inputMode="numeric" value={areaMin} onChange={(e) => setAreaMin(e.target.value)} />
        </Field>
        <Field label="حداکثر متراژ" htmlFor="rq-amax">
          <Input id="rq-amax" dir="ltr" inputMode="numeric" value={areaMax} onChange={(e) => setAreaMax(e.target.value)} />
        </Field>
        <Field label="حداقل تعداد خواب" htmlFor="rq-rooms">
          <Input id="rq-rooms" dir="ltr" inputMode="numeric" value={roomsMin} onChange={(e) => setRoomsMin(e.target.value)} />
        </Field>
        <Field label="حداقل سال ساخت (شمسی)" htmlFor="rq-year">
          <Input id="rq-year" dir="ltr" inputMode="numeric" placeholder="۱۳۹۰" value={yearMin} onChange={(e) => setYearMin(e.target.value)} />
        </Field>

        <div className="col-span-full flex flex-wrap gap-x-6 gap-y-2">
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <Checkbox checked={elevator} onCheckedChange={(v) => setElevator(v === true)} />
            آسانسور داشته باشد
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <Checkbox checked={parking} onCheckedChange={(v) => setParking(v === true)} />
            پارکینگ داشته باشد
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <Checkbox checked={storage} onCheckedChange={(v) => setStorage(v === true)} />
            انباری داشته باشد
          </label>
        </div>

        <Field label="توضیحات بیشتر" htmlFor="rq-desc" className="col-span-full">
          <Textarea id="rq-desc" placeholder="هر نکته‌ای که برایتان مهم است…" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </Field>

        <div className="col-span-full">
          <Button type="submit" disabled={create.isPending} className="h-11 w-full sm:w-auto">
            ثبت درخواست
          </Button>
        </div>
      </form>
    </Section>
  );
}

function budgetText(r: PropertyRequest): string {
  const toW = (n: number) => `${new Intl.NumberFormat("fa-IR-u-ca-persian-nu-arabext").format(n)} تومان`;
  if (r.deal_type === "rent") {
    const bits = [];
    if (r.deposit_max) bits.push(`ودیعه تا ${toW(r.deposit_max)}`);
    if (r.rent_max) bits.push(`اجاره تا ${toW(r.rent_max)}`);
    return bits.join(" / ") || "—";
  }
  if (r.budget_min && r.budget_max) return `${toW(r.budget_min)} تا ${toW(r.budget_max)}`;
  if (r.budget_max) return `تا ${toW(r.budget_max)}`;
  if (r.budget_min) return `از ${toW(r.budget_min)}`;
  return "—";
}

function MyRequests() {
  const confirm = useConfirm();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["portal", "requests"],
    queryFn: () => api<{ items: PropertyRequest[]; total: number }>("/portal/requests/mine"),
  });

  const del = useMutation({
    mutationFn: (id: number) => api(`/portal/requests/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["portal", "requests"] });
      void qc.invalidateQueries({ queryKey: ["portal", "me"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "حذف ناموفق بود"),
  });

  return (
    <Section title="درخواست‌های من">
      {q.isPending ? (
        <ListSkeleton />
      ) : q.isError ? (
        <ErrorNote error={q.error} />
      ) : q.data.items.length === 0 ? (
        <Empty icon={Inbox}>هنوز درخواستی ثبت نکرده‌اید.</Empty>
      ) : (
        <ul className="flex flex-col gap-3">
          {q.data.items.map((r) => {
            const status = STATUS[r.status] ?? { label: r.status, tone: "neutral" as Tone };
            const title = [DEAL_LABEL[r.deal_type] ?? r.deal_type, r.city, r.districts].filter(Boolean).join(" · ");
            return (
              <li key={r.id} className="rounded-xl border p-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{title || "درخواست ملک"}</span>
                  <ToneBadge tone={status.tone}>{status.label}</ToneBadge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{budgetText(r)}</p>
                {r.description && <p className="mt-1.5 text-sm text-muted-foreground">{r.description}</p>}
                {r.admin_note && <p className="mt-1.5 text-sm text-primary">پاسخ کارشناس: {r.admin_note}</p>}
                <div className="mt-2.5 flex items-center justify-between gap-2">
                  <time className="text-[11px] text-muted-foreground" dateTime={r.created_at}>
                    {faDate(new Date(r.created_at), { day: "numeric", month: "long", year: "numeric" })}
                  </time>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    disabled={del.isPending}
                    onClick={async () => {
                      if (await confirm({ title: "این درخواست حذف شود؟", danger: true, icon: Trash2 })) del.mutate(r.id);
                    }}
                  >
                    <Trash2 className="size-3.5" /> حذف
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

function TicketCard({ ticket }: { ticket: Ticket | null }) {
  const qc = useQueryClient();
  const [message, setMessage] = useState("");

  const submit = useMutation({
    mutationFn: () => api<Ticket>("/portal/tickets", { json: { message: message.trim() || null } }),
    onSuccess: () => {
      toast.success("درخواست شما ارسال شد");
      setMessage("");
      void qc.invalidateQueries({ queryKey: ["portal", "me"] });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "ارسال درخواست ناموفق بود"),
  });

  if (ticket?.status === "pending") {
    return (
      <Section title="دسترسی به پنل مدیریت">
        <div className="rounded-xl border p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">درخواست ارتقا</span>
            <ToneBadge tone="warning">در حال بررسی</ToneBadge>
          </div>
          <p className="mt-1.5 text-sm text-muted-foreground">درخواست شما برای مدیر ارسال شده و در انتظار بررسی است.</p>
        </div>
      </Section>
    );
  }

  if (ticket?.status === "approved") {
    return (
      <Section title="دسترسی به پنل مدیریت">
        <div className="rounded-xl border p-3.5">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">درخواست ارتقا</span>
            <ToneBadge tone="success">تأیید شد</ToneBadge>
          </div>
          <p className="mt-1.5 text-sm text-muted-foreground">دسترسی شما فعال شد.</p>
          <Link href="/panel" className="mt-2.5 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline">
            ورود به پنل مدیریت <ExternalLink className="size-3.5" />
          </Link>
        </div>
      </Section>
    );
  }

  return (
    <Section
      title="دسترسی به پنل مدیریت"
      hint="اگر مشاور املاک هستید و می‌خواهید به پنل مدیریت دسترسی داشته باشید، درخواست خود را ثبت کنید تا مدیر بررسی کند."
    >
      {ticket?.status === "rejected" && (
        <p className="mb-3 text-sm text-muted-foreground">
          درخواست قبلی شما تأیید نشد{ticket.decision_note ? `؛ ${ticket.decision_note}` : ""}.
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit.mutate();
        }}
        className="flex flex-col gap-3"
      >
        <Field label="توضیح کوتاه (اختیاری)" htmlFor="tk-msg">
          <Textarea
            id="tk-msg"
            placeholder="مثلاً: مشاور املاک در ارومیه هستم و به پنل نیاز دارم"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </Field>
        <Button type="submit" disabled={submit.isPending} className="w-full sm:w-auto">
          ارسال درخواست دسترسی
        </Button>
      </form>
    </Section>
  );
}

export function PortalDashboard({ site }: { site: { brandName: string; brandNameLatin: string } }) {
  const router = useRouter();
  const qc = useQueryClient();
  const session = useSession();

  // a stale/cleared cookie sends the visitor back to the auth screen, the
  // same way AppShell sends staff back to /panel/login on a 401
  useEffect(
    () =>
      onApiError((e) => {
        if (e.status === 401) {
          qc.removeQueries({ queryKey: SESSION_KEY });
          router.replace("/portal");
        }
      }),
    [qc, router],
  );

  useEffect(() => {
    if (session.isError) router.replace("/portal");
  }, [session.isError, router]);

  const me = useQuery({ queryKey: ["portal", "me"], queryFn: () => api<Me>("/portal/me"), enabled: !!session.data });

  async function logout() {
    try {
      await api("/session/logout", { method: "POST" });
    } finally {
      qc.clear();
      router.replace("/portal");
    }
  }

  if (session.isPending || session.isError || me.isPending) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <ListSkeleton rows={6} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8 sm:px-6">
      <Reveal>
        <Tilt className="rounded-2xl" max={3}>
          <header className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-card p-4 dark:bg-linear-to-b dark:from-white/[0.035] dark:to-white/[0.008]">
            <div className="flex items-center gap-3">
              <div className="grid size-11 place-items-center rounded-xl bg-linear-to-br from-indigo-500 to-violet-600 text-sm font-black text-white shadow-[0_0_24px_-4px_rgb(99_102_241/0.7)]">
                {site.brandNameLatin.slice(0, 1)}
              </div>
              <div className="leading-tight">
                <div className="font-bold">{session.data.user.full_name || "کاربر"}</div>
                <div dir="ltr" className="text-end text-xs text-muted-foreground">
                  {session.data.user.phone}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <ThemeToggle />
              <Button variant="ghost" size="sm" onClick={() => void logout()}>
                <LogOut className="size-4" /> خروج
              </Button>
            </div>
          </header>
        </Tilt>
      </Reveal>

      {me.isError ? (
        <ErrorNote error={me.error} />
      ) : (
        <>
          <Reveal delay={0.05}>
            <RequestForm site={{ brandName: site.brandName }} />
          </Reveal>
          <Reveal delay={0.1}>
            <MyRequests />
          </Reveal>
          <Reveal delay={0.15}>
            <TicketCard ticket={me.data.ticket} />
          </Reveal>
        </>
      )}
    </div>
  );
}
