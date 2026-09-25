// Shapes this section reads from the backend (app/api/routes/auth.py,
// app/api/routes/scraper.py's OTP-passthrough endpoints). Kept close to the
// wire so a page never has to guess a field that is not there.

export type DivarCookie = {
  id: number;
  phone_number: string;
  is_valid: boolean;
  expires_at: string | null;
  last_checked_at: string | null;
  created_at: string | null;
  reveals: number;
  challenged_at: string | null;
  last_used_at: string | null;
  owner_user_id: number | null;
  identity_required_at: string | null;
  is_enabled: boolean;
  owner_name: string | null;
};

export type CookiesResponse = { sees_every_session: boolean; cookies: DivarCookie[] };

export type RegistryUser = { id: number; name: string; username: string; role: string; is_active: boolean };

export type RegistryNumber = {
  id: number;
  phone_number: string;
  owner_user_id: number | null;
  owner_name: string | null;
  is_valid: boolean;
  is_enabled: boolean;
  reveals: number;
  last_checked_at: string | null;
  identity_required_at: string | null;
  in_use: boolean;
  suggested_owner: { id: number; name: string | null; why: "divar_phone" | "forwarder" } | null;
};

export type RegistryResponse = { numbers: RegistryNumber[]; users: RegistryUser[] };

export type AuthResponse = { success: boolean; message: string; requires_code: boolean };

export type ForwarderInfo = {
  online: boolean;
  battery?: number | null;
  network?: string | null;
  version?: string | null;
  last_seen?: number;
  account?: string;
};

export type PendingOtp = {
  key: string;
  phone_hint: string;
  remaining: number;
  resends: number;
  resends_left: number;
};

export type IdentityRequired = {
  phone: string;
  job_id: string | null;
  at: number;
  age: number;
  text: string;
};

export type OtpPendingResponse = {
  forwarders: Record<string, ForwarderInfo>;
  pending: PendingOtp[];
  timeout: number;
  identity_required: IdentityRequired[];
};
