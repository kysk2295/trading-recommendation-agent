const numberFormatter = new Intl.NumberFormat("ko-KR");
const priceFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 4,
  minimumFractionDigits: 2,
});
const dollarFormatter = new Intl.NumberFormat("en-US", {
  currency: "USD",
  maximumFractionDigits: 2,
  minimumFractionDigits: 2,
  signDisplay: "auto",
  style: "currency",
});

export function count(value: number): string {
  return numberFormatter.format(value);
}

export function price(value: number): string {
  return priceFormatter.format(value);
}

export function priceText(value: string): string {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? price(parsed) : value;
}

export function dollars(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? dollarFormatter.format(parsed) : "—";
}

export function shortTime(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));
}

export function marketTime(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function stateLabel(state: string): string {
  const labels: Readonly<Record<string, string>> = {
    active: "진입",
    after: "장후",
    armed: "예약",
    blocked: "차단",
    closed: "휴장",
    completed: "완료",
    incomplete: "검증 미완료",
    failed: "실패",
    idle: "대기",
    open: "개장",
    pending: "대기",
    pre: "장전",
    queued: "접수",
    ready: "준비",
    running: "실행 중",
    setup: "조건 대기",
    stopped: "손절",
    target_1r: "1R 도달",
    target_2r: "2R 도달",
    time_exit: "시간 청산",
    unavailable: "자료 없음",
    verified: "검증됨",
  };
  return labels[state] ?? state;
}

export function statusClass(state: string): string {
  if (["completed", "ready", "open", "target_1r", "target_2r", "verified"].includes(state)) {
    return "state-ready";
  }
  if (["failed", "blocked", "stopped"].includes(state)) {
    return "state-failed";
  }
  return "state-armed";
}
