"use client";

import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

function isIosSafari() {
  const ua = window.navigator.userAgent;
  const isIOS = /iPad|iPhone|iPod/.test(ua);
  // excludes Chrome/Firefox/etc. on iOS, which all identify as "Safari" in
  // the UA string too but carry their own browser token alongside it
  const isSafariEngine = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
  return isIOS && isSafariEngine;
}

/**
 * The install entry in the shell's user menu (see UserMenu in app-shell.tsx).
 * Chrome/Edge/Android capture `beforeinstallprompt` and trigger it directly;
 * iOS Safari never fires that event, so there `onIosHint` asks the parent to
 * show the «Share → Add to Home Screen» dialog instead — the item itself
 * renders no dialog, since a dropdown's content unmounts on close and would
 * take the dialog's open state with it.
 */
export function InstallMenuItem({ onIosHint }: { onIosHint: () => void }) {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [eligible, setEligible] = useState(false);
  // Lazy initializer, not an effect: read once at mount, nothing to
  // subscribe to afterward (unlike beforeinstallprompt/appinstalled below).
  const [iosEligible] = useState(() => typeof window !== "undefined" && !isStandalone() && isIosSafari());

  useEffect(() => {
    if (isStandalone()) return;

    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setEligible(true);
    };
    const onInstalled = () => {
      setDeferred(null);
      setEligible(false);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!eligible && !iosEligible) return null;

  return (
    <DropdownMenuItem
      onSelect={() => {
        if (deferred) {
          void deferred.prompt();
          setDeferred(null);
          setEligible(false);
        } else {
          onIosHint();
        }
      }}
    >
      <Download /> نصب برنامه
    </DropdownMenuItem>
  );
}
