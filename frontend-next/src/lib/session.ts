"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type Role = "root" | "super_admin" | "admin" | "visitor";

/** GET /api/users/me — app/schemas UserResponse. */
export type User = {
  id: number;
  username: string;
  email: string | null;
  full_name: string | null;
  role: Role;
  is_active: boolean;
  divar_phone: string | null;
  phone: string | null;
  phone_verified: boolean;
  email_verified: boolean;
  email_2fa_enabled: boolean;
  permissions: string[];
  last_login: string | null;
  created_at: string | null;
  headline: string | null;
  bio: string | null;
  links: Record<string, string> | null;
  presence: "available" | "busy" | "away";
  avatar_url: string | null;
};

export type Session = { user: User; csrf_token: string };

export const SESSION_KEY = ["session"] as const;

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => api<Session>("/session"),
    staleTime: 60_000,
    retry: false,
  });
}

const FULL_ACCESS: Role[] = ["root", "super_admin"];

/** Who sees what, in one place (the backend enforces the same keys on its
 *  routers, so a hidden link and a refused request cannot disagree). */
export function can(user: User | undefined, need: { perm?: string; roles?: Role[] }): boolean {
  if (!user) return false;
  if (need.roles && !need.roles.includes(user.role)) return false;
  if (!need.perm) return true;
  return FULL_ACCESS.includes(user.role) || user.permissions.includes(need.perm);
}

export const ROLE_LABEL: Record<Role, string> = {
  root: "Root",
  super_admin: "مدیر ارشد",
  admin: "مدیر",
  visitor: "بازدیدکننده",
};

export const displayName = (u: User) => u.full_name?.trim() || u.username;
