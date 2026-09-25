"use client";

// Create/rename a کمد (cabinet) or a زونکن/پوشه (binder/folder) — three small
// forms sharing one look, since a folder is only a binder with a parent_id
// (app/models/crm_models.py Binder).

import { Archive, BookMarked, FolderTree } from "lucide-react";
import { useState } from "react";
import { cn } from "cn";
import { Field, NativeSelect, RingDialog } from "@/components/panel/kit";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/toaster";
import { api, ApiError } from "@/lib/api";
import { BINDER_KINDS, CABINET_PALETTE, DEAL_TYPES, type Binder, type Cabinet } from "./types";

function Swatches({ value, onChange }: { value: string; onChange: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {CABINET_PALETTE.map((c) => (
        <button
          key={c}
          type="button"
          aria-label={c}
          onClick={() => onChange(c)}
          className={cn("size-6 rounded-full ring-offset-2 ring-offset-popover transition-transform hover:scale-110", value === c && "ring-2 ring-foreground")}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}

export function CabinetDialog({
  open, onOpenChange, cabinet, onSaved,
}: { open: boolean; onOpenChange: (o: boolean) => void; cabinet?: Cabinet | null; onSaved: () => void }) {
  const editing = !!cabinet;
  const [name, setName] = useState(cabinet?.name ?? "");
  const [color, setColor] = useState(cabinet?.color ?? CABINET_PALETTE[0]);
  const [personal, setPersonal] = useState(!!cabinet?.owner_user_id);
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) {
      toast.error("نام کمد الزامی است");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api(`/filing/cabinets/${cabinet!.id}`, { method: "PATCH", json: { name: name.trim(), color, personal } });
        toast.success("کمد به‌روزرسانی شد");
      } else {
        await api("/filing/cabinets", { json: { name: name.trim(), color, personal } });
        toast.success("کمد ساخته شد");
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={Archive}
      title={editing ? "ویرایش کمد" : "کمد جدید"}
      footer={
        <Button className="w-full" disabled={saving} onClick={save}>
          {editing ? "ذخیرهٔ تغییرات" : "ساخت کمد"}
        </Button>
      }
    >
      <div className="grid gap-3">
        <Field label="نام کمد" htmlFor="cab-name">
          <Input id="cab-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="رنگ">
          <Swatches value={color} onChange={setColor} />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={personal} onCheckedChange={(v) => setPersonal(v === true)} />
          کمد شخصی (فقط خودم می‌بینم)
        </label>
      </div>
    </RingDialog>
  );
}

export function BinderDialog({
  open, onOpenChange, binder, cabinetId, parentId, onSaved,
}: {
  open: boolean; onOpenChange: (o: boolean) => void; binder?: Binder | null;
  cabinetId?: number; parentId?: number | null; onSaved: () => void;
}) {
  const editing = !!binder;
  const isFolder = !!(binder?.parent_id ?? parentId);
  const [name, setName] = useState(binder?.name ?? "");
  const [color, setColor] = useState(binder?.color ?? "#38bdf8");
  const [kind, setKind] = useState(binder?.kind ?? "property");
  const [dealType, setDealType] = useState(binder?.deal_type ?? "");
  const [description, setDescription] = useState(binder?.description ?? "");
  const [saving, setSaving] = useState(false);

  async function save() {
    if (!name.trim()) {
      toast.error(isFolder ? "نام پوشه الزامی است" : "نام زونکن الزامی است");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api(`/filing/binders/${binder!.id}`, { method: "PATCH", json: { name: name.trim(), color, kind, deal_type: dealType, description: description.trim() || null } });
        toast.success("به‌روزرسانی شد");
      } else {
        await api("/filing/binders", {
          json: { name: name.trim(), color, kind, deal_type: dealType, description: description.trim() || null, cabinet_id: cabinetId, parent_id: parentId ?? null },
        });
        toast.success(isFolder ? "پوشه ساخته شد" : "زونکن ساخته شد");
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      toast.error("ذخیره نشد", e instanceof ApiError ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  }

  return (
    <RingDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={isFolder ? FolderTree : BookMarked}
      title={editing ? "ویرایش" : isFolder ? "پوشهٔ جدید" : "زونکن جدید"}
      footer={
        <Button className="w-full" disabled={saving} onClick={save}>
          {editing ? "ذخیرهٔ تغییرات" : "ساخت"}
        </Button>
      }
    >
      <div className="grid gap-3">
        <Field label="نام" htmlFor="bin-name">
          <Input id="bin-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label="رنگ">
          <Swatches value={color} onChange={setColor} />
        </Field>
        {!isFolder && (
          <>
            <Field label="نوع" htmlFor="bin-kind">
              <NativeSelect id="bin-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
                {Object.entries(BINDER_KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect>
            </Field>
            <Field label="نوع معامله" htmlFor="bin-deal">
              <NativeSelect id="bin-deal" value={dealType} onChange={(e) => setDealType(e.target.value)}>
                {Object.entries(DEAL_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect>
            </Field>
          </>
        )}
        <Field label="توضیحات (اختیاری)" htmlFor="bin-desc">
          <Textarea id="bin-desc" value={description ?? ""} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={300} />
        </Field>
      </div>
    </RingDialog>
  );
}
