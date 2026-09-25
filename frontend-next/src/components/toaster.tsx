"use client";

import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { Toast as T } from "radix-ui";
import { useSyncExternalStore } from "react";
import { cn } from "cn";

// Small toasts on Radix Toast. (sonner injects a <style> at runtime that the
// nonce CSP blocks; this one only uses the app's stylesheet.)

type Tone = "success" | "error" | "info";
type Item = { id: number; tone: Tone; title: string; body?: string };

let items: Item[] = [];
let seq = 0;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

function push(tone: Tone, title: string, body?: string) {
  items = [...items.slice(-3), { id: ++seq, tone, title, body }];
  emit();
}
function drop(id: number) {
  items = items.filter((i) => i.id !== id);
  emit();
}

export const toast = {
  success: (title: string, body?: string) => push("success", title, body),
  error: (title: string, body?: string) => push("error", title, body),
  info: (title: string, body?: string) => push("info", title, body),
};

const ICON = { success: CircleCheck, error: CircleAlert, info: Info } as const;
const TONE = { success: "text-success", error: "text-destructive", info: "text-primary" } as const;

export function Toaster() {
  const list = useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => items,
    () => items,
  );
  return (
    <T.Provider swipeDirection="left" duration={5000}>
      {list.map((it) => {
        const Icon = ICON[it.tone];
        return (
          <T.Root
            key={it.id}
            onOpenChange={(open) => !open && drop(it.id)}
            className="flex items-start gap-3 rounded-2xl border bg-popover p-4 text-popover-foreground shadow-xl data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:animate-in data-[state=open]:slide-in-from-bottom-4"
          >
            <Icon className={cn("mt-0.5 size-5 shrink-0", TONE[it.tone])} />
            <div className="min-w-0 flex-1">
              <T.Title className="text-sm font-semibold">{it.title}</T.Title>
              {it.body && <T.Description className="mt-0.5 text-xs leading-5 text-muted-foreground">{it.body}</T.Description>}
            </div>
            <T.Close aria-label="بستن" className="rounded-md p-0.5 text-muted-foreground hover:text-foreground">
              <X className="size-4" />
            </T.Close>
          </T.Root>
        );
      })}
      <T.Viewport className="fixed bottom-4 end-4 z-[100] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2 outline-none" />
    </T.Provider>
  );
}
