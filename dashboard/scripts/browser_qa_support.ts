import AxeBuilder from "@axe-core/playwright";
import type { Page } from "playwright";

type AxeFinding = {
  readonly id: string;
  readonly nodes: readonly { readonly target: unknown }[];
};

type AxeScan = {
  readonly violations: readonly AxeFinding[];
  readonly incomplete: readonly AxeFinding[];
};

type AxeCounts = {
  readonly violations: number;
  readonly incomplete: number;
  readonly violationKeys: readonly string[];
  readonly incompleteKeys: readonly string[];
};

export class BrowserQaError extends Error {
  override readonly name = "BrowserQaError";
}

export async function analyzeAtScrollPositions(
  page: Page,
): Promise<AxeCounts & { readonly scrollPositions: number }> {
  const scrollBody = page.locator(".workspace-scroll-body");
  const hasScrollBody = (await scrollBody.count()) === 1;
  const positions =
    hasScrollBody
      ? await scrollBody.evaluate((element) => {
          const height = Math.max(element.clientHeight, 1);
          return Array.from({ length: Math.ceil(element.scrollHeight / height) + 1 }, (_, index) =>
            Math.min(index * height, element.scrollHeight),
          );
        })
      : [0];
  const scans: AxeScan[] = [];
  for (const position of positions) {
    if (hasScrollBody) {
      await scrollBody.evaluate(async (element, offset) => {
        element.scrollTop = offset;
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        );
      }, position);
    }
    const materializedStyle = hasScrollBody
      ? await page.addStyleTag({
          url: new URL("/__qa__/materialize.css", page.url()).toString(),
        })
      : undefined;
    try {
      scans.push(await new AxeBuilder({ page }).analyze());
    } finally {
      await materializedStyle?.evaluate((element) => element.parentNode?.removeChild(element));
    }
  }
  return { ...aggregateAxeFindings(scans), scrollPositions: positions.length };
}

export function aggregateAxeFindings(scans: readonly AxeScan[]): AxeCounts {
  const violations = new Set<string>();
  const incomplete = new Set<string>();
  for (const scan of scans) {
    addFindings(violations, scan.violations);
    addFindings(incomplete, scan.incomplete);
  }
  return {
    violations: violations.size,
    incomplete: incomplete.size,
    violationKeys: [...violations].sort(),
    incompleteKeys: [...incomplete].sort(),
  };
}

function addFindings(target: Set<string>, findings: readonly AxeFinding[]): void {
  for (const finding of findings) {
    for (const node of finding.nodes) {
      target.add(`${finding.id}:${JSON.stringify(node.target)}`);
    }
  }
}

export async function resetScrollableContent(page: Page): Promise<void> {
  const scrollBody = page.locator(".workspace-scroll-body");
  if ((await scrollBody.count()) !== 1) return;
  await scrollBody.evaluate(async (element) => {
    element.scrollTop = 0;
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  });
}

export function parseWidths(raw: string): readonly number[] {
  const parsed = raw.split(",").map((value) => Number.parseInt(value, 10));
  if (parsed.some((value) => !Number.isInteger(value) || value < 320 || value > 2560)) {
    throw new BrowserQaError("widths must be comma-separated integers from 320 to 2560");
  }
  return parsed;
}

export function requiredEnvironment(name: string): string {
  const value = process.env[name];
  if (value === undefined || value.length === 0) {
    throw new BrowserQaError(`${name} is required`);
  }
  return value;
}

export function requiredOption(value: string | undefined, name: string): string {
  if (value === undefined || value.length === 0) {
    throw new BrowserQaError(`${name} is required`);
  }
  return value;
}

export function requireEqual<T>(actual: T, expected: T, label: string): void {
  if (actual !== expected) {
    throw new BrowserQaError(`${label}: expected ${String(expected)}, received ${String(actual)}`);
  }
}

export function requirePositive(actual: number, label: string): void {
  if (actual < 1) {
    throw new BrowserQaError(`${label}: expected at least one, received ${actual}`);
  }
}
