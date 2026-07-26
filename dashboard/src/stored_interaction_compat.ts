import type { Interaction } from "./schema";
import { interactionSchema } from "./schema";

export function parseStoredInteractionPayloads(
  payloads: readonly unknown[],
): readonly Interaction[] {
  return payloads.flatMap((payload) => {
    const parsed = interactionSchema.safeParse(payload);
    return parsed.success ? [parsed.data] : [];
  });
}
