"use client";

import { createContext, useContext } from "react";
import { setNonce } from "get-nonce";

// Some libraries add a <style> element at runtime (Radix's scroll lock under
// every dialog, the OTP input). The strict CSP drops any <style> without this
// request's nonce, so it is handed to them here.
const NonceContext = createContext<string | undefined>(undefined);

export function NonceProvider({ nonce, children }: { nonce?: string; children: React.ReactNode }) {
  if (typeof window !== "undefined" && nonce) setNonce(nonce);
  return <NonceContext.Provider value={nonce}>{children}</NonceContext.Provider>;
}

export const useNonce = () => useContext(NonceContext);
