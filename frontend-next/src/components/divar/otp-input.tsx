"use client";

// The six-box segmented code input used by every OTP step in this section
// (login-a-number, and the global Divar OTP popup): paste-aware, digits
// only, auto-submits once all six are filled.

import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { useNonce } from "@/components/nonce";
import { parseDigits } from "@/lib/format";

export function SixDigitOtp({
  value, onChange, onComplete, disabled, autoFocus, "aria-label": ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  onComplete: (v: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  "aria-label": string;
}) {
  const nonce = useNonce();
  return (
    <div dir="ltr" className="mx-auto w-fit">
      <InputOTP
        maxLength={6}
        value={value}
        onChange={onChange}
        onComplete={onComplete}
        disabled={disabled}
        autoFocus={autoFocus}
        nonce={nonce}
        pasteTransformer={(t) => parseDigits(t).replace(/\D/g, "")}
        aria-label={ariaLabel}
      >
        <InputOTPGroup>
          {Array.from({ length: 6 }, (_, i) => (
            <InputOTPSlot key={i} index={i} className="size-11 text-base" />
          ))}
        </InputOTPGroup>
      </InputOTP>
    </div>
  );
}
