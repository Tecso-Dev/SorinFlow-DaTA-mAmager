"use client";

// The permission catalog super_admin ticks for an admin — shared between the
// ticket decision box and the perms editor, so both list the same keys.

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type PermCatalog = { items: { key: string; label: string }[]; defaults: string[] };

export function usePermCatalog() {
  return useQuery({
    queryKey: ["users", "perm-catalog"],
    queryFn: () => api<PermCatalog>("/users/permissions/catalog"),
    staleTime: 5 * 60_000,
  });
}
