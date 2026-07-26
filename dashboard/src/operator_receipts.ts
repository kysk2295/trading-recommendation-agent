import type { AutonomousTaskReceipt, DirectedJobEvent, Interaction } from "./schema";

export type OperatorReceiptSnapshot = Readonly<{
  interactions: readonly Interaction[];
  directedJobs: readonly DirectedJobEvent[];
  autonomousTasks: readonly AutonomousTaskReceipt[];
}>;
