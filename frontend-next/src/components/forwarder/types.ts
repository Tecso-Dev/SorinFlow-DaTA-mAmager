// Shapes returned by app/api/routes/forwarder.py and, for the codes log,
// app/api/routes/sms.py's GET /sms/events?stage=inbound.

export type HealthState = "ok" | "offline" | "never_seen" | "no_codes_yet" | "disabled";

/** app/services/forwarder.py health(). */
export type Health = {
  state: HealthState;
  message_fa: string;
  online: boolean;
  seconds_since_seen: number | null;
  seconds_since_code: number | null;
  battery: number | null;
  network: string | null;
};

/** ForwarderDevice.to_dict() plus the health block the list/create/rotate
 *  routes attach. `secret` is non-null only right after create/rotate or
 *  inside a /config read — everywhere else it is null and `secret_masked`
 *  is what there is to show. */
export type Device = {
  id: number;
  device_id: string;
  user_id: number;
  label: string | null;
  sim_phone: string | null;
  sim_phone2: string | null;
  is_active: boolean;
  secret: string | null;
  secret_masked: string;
  last_seen_at: string | null;
  last_code_at: string | null;
  codes_forwarded: number;
  battery: number | null;
  network: string | null;
  app_version: string | null;
  note: string | null;
  created_at: string | null;
  health: Health;
};

export type DevicesResponse = { devices: Device[]; count: number };

/** ForwarderDevice.to_dict() alone — what /config's `device` field carries.
 *  Unlike the list/create/rotate routes, /config does not attach `health`. */
export type DeviceRow = Omit<Device, "health">;

export type Rule = {
  name_fa: string;
  sender: string;
  text_filter: string;
  template: string;
  why_fa: string;
  sim_slot?: number;
};

/** GET /forwarder/devices/{id}/config. */
export type DeviceConfig = {
  device: DeviceRow;
  setup_payload: string;
  android_apk_url: string;
  android_apk_version: string;
  android_source_url: string;
  android_release_url: string;
  ios: { available: boolean; message_fa: string };
  endpoints: { inbound: string; heartbeat: string };
  headers: Record<string, string>;
  rules: Rule[];
  rules_sim2: Rule[];
  accounts: string[];
  advanced_fa: { retries: number; store_failed: boolean; ignore_ssl: boolean; note: string };
};

export type TestResult = { ok: boolean; health: Health; hint_fa: string; checked_at: string };

/** GET /sms/events?stage=inbound — one row per inbound POST from a phone. */
export type SmsEvent = {
  id: number;
  at: string | null;
  stage: string;
  level: string;
  message: string;
  route: string | null;
  status: string | null;
  actor: string | null;
  details: { reason?: string; kind?: string; code?: string; account?: string; latency_ms?: number; [k: string]: unknown };
};
