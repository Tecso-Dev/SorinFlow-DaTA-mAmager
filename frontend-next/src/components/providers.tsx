"use client";

import { MotionConfig } from "motion/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { NonceProvider } from "./nonce";
import { Toaster } from "./toaster";

export function Providers({ nonce, children }: { nonce?: string; children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: true,
            // a 4xx will not get better by asking again
            retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 2,
          },
        },
      }),
  );
  return (
    <NonceProvider nonce={nonce}>
      <QueryClientProvider client={client}>
        <MotionConfig reducedMotion="user" nonce={nonce}>
          {children}
          <Toaster />
        </MotionConfig>
      </QueryClientProvider>
    </NonceProvider>
  );
}
