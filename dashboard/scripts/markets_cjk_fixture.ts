export function cjkFixtureGeneratedAt(baseEpochMs: number, ordinal: number): string {
  return new Date(baseEpochMs + ordinal).toISOString();
}
