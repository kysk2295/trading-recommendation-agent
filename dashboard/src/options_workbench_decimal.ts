export const OPERATIONAL_DECIMAL_SCALE = 100_000_000n;
export const OPERATIONAL_DECIMAL_PATTERN = /^-?[0-9]{1,6}(?:\.[0-9]{1,8})?$/;
export const NONNEGATIVE_OPERATIONAL_DECIMAL_PATTERN = /^[0-9]{1,6}(?:\.[0-9]{1,8})?$/;

export type OperationalDecimal = Readonly<{ scaled: bigint; value: number }>;
export type OperationalDecimalConversion =
  | Readonly<{ kind: "ready"; decimal: OperationalDecimal }>
  | Readonly<{ kind: "blocked"; reason: "unsafe_operational_decimal" }>;

export function parseOperationalDecimal(value: string): OperationalDecimalConversion {
  if (!OPERATIONAL_DECIMAL_PATTERN.test(value)) return blocked();
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole = "0", fraction = ""] = unsigned.split(".");
  const magnitude = BigInt(whole) * OPERATIONAL_DECIMAL_SCALE + BigInt(fraction.padEnd(8, "0"));
  const scaled = negative ? -magnitude : magnitude;
  return {
    kind: "ready",
    decimal: { scaled, value: Number(scaled) / Number(OPERATIONAL_DECIMAL_SCALE) },
  };
}

export function operationalDecimalFromNumber(value: number): OperationalDecimalConversion {
  if (!Number.isFinite(value)) return blocked();
  const normalized = value.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
  const parsed = parseOperationalDecimal(normalized);
  if (parsed.kind === "blocked" || Math.abs(parsed.decimal.value - value) > 0.000000005)
    return blocked();
  return parsed;
}

export function formatOperationalRatio(
  numerator: bigint,
  divisor: bigint,
  fractionDigits: number,
): string {
  const displayScale = 10n ** BigInt(fractionDigits);
  const denominator = divisor * OPERATIONAL_DECIMAL_SCALE;
  const negative = numerator < 0n;
  const magnitude = negative ? -numerator : numerator;
  const rounded = (magnitude * displayScale * 2n + denominator) / (denominator * 2n);
  const whole = rounded / displayScale;
  const fraction = (rounded % displayScale).toString().padStart(fractionDigits, "0");
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}

export function safeOperationalNumber(scaled: bigint): number | null {
  const maximum = BigInt(Number.MAX_SAFE_INTEGER);
  if (scaled > maximum || scaled < -maximum) return null;
  return Number(scaled) / Number(OPERATIONAL_DECIMAL_SCALE);
}

function blocked(): OperationalDecimalConversion {
  return { kind: "blocked", reason: "unsafe_operational_decimal" };
}
