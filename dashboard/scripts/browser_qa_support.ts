import AxeBuilder from "@axe-core/playwright";
import type { Page } from "playwright";

export class BrowserQaError extends Error {
  override readonly name = "BrowserQaError";
}

export async function analyzeAtScrollPositions(
  page: Page,
): Promise<{ readonly violations: number; readonly incomplete: number }> {
  const scrollBody = page.locator(".workspace-scroll-body");
  const positions =
    (await scrollBody.count()) === 1
      ? await scrollBody.evaluate((element) => {
          const height = Math.max(element.clientHeight, 1);
          return Array.from({ length: Math.ceil(element.scrollHeight / height) + 1 }, (_, index) =>
            Math.min(index * height, element.scrollHeight),
          );
        })
      : [0];
  const violations = new Set<string>();
  let persistentIncomplete: Set<string> | undefined;
  for (const position of positions) {
    if ((await scrollBody.count()) === 1) {
      await scrollBody.evaluate(async (element, offset) => {
        element.scrollTop = offset;
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      }, position);
    }
    const result = await new AxeBuilder({ page }).analyze();
    for (const violation of result.violations) {
      for (const node of violation.nodes) {
        violations.add(`${violation.id}:${JSON.stringify(node.target)}`);
      }
    }
    const currentIncomplete = new Set(
      result.incomplete.flatMap((finding) =>
        finding.nodes.map((node) => `${finding.id}:${JSON.stringify(node.target)}`),
      ),
    );
    persistentIncomplete =
      persistentIncomplete === undefined
        ? currentIncomplete
        : new Set([...persistentIncomplete].filter((finding) => currentIncomplete.has(finding)));
  }
  return { violations: violations.size, incomplete: persistentIncomplete?.size ?? 0 };
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
