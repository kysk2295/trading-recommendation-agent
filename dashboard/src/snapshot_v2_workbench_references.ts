export type WorkbenchGraphInput = {
  readonly market: TraceReferenceInput;
  readonly chain: TraceReferenceInput & {
    readonly rows: readonly {
      readonly call: TraceReferenceInput | null;
      readonly put: TraceReferenceInput | null;
    }[];
  };
  readonly agent: TraceReferenceInput;
  readonly experiment: TraceReferenceInput;
  readonly scenario: null | {
    readonly trace_id: string;
    readonly legs: readonly { readonly trace_id: string }[];
  };
  readonly promotions: readonly TraceReferenceInput[];
};

type TraceReferenceInput = { readonly state: string; readonly trace_id: string };

export type TraceReferenceGroup = {
  readonly terminals: ReadonlySet<string>;
  readonly references: readonly string[];
};

export function derivativeWorkbenchReferenceGroups(
  workbench: WorkbenchGraphInput,
  terminals: ReadonlySet<string>,
  terminalsForState: (state: string, domainTerminals: ReadonlySet<string>) => ReadonlySet<string>,
): readonly TraceReferenceGroup[] {
  const sectionGroups = [
    workbench.market,
    workbench.chain,
    workbench.agent,
    workbench.experiment,
  ].map((section) => referenceGroup(section, terminals, terminalsForState));
  const cellGroups = workbench.chain.rows
    .flatMap((row) => [row.call, row.put])
    .flatMap((cell) => (cell === null ? [] : [referenceGroup(cell, terminals, terminalsForState)]));
  const scenarioGroups =
    workbench.scenario === null
      ? []
      : [
          {
            terminals,
            references: [
              workbench.scenario.trace_id,
              ...workbench.scenario.legs.map((leg) => leg.trace_id),
            ],
          },
        ];
  const promotionGroups = workbench.promotions.map((promotion) => ({
    terminals: promotion.state === "approved" ? terminals : new Set(["blocker_terminal"]),
    references: [promotion.trace_id],
  }));
  return [...sectionGroups, ...cellGroups, ...scenarioGroups, ...promotionGroups];
}

function referenceGroup(
  reference: TraceReferenceInput,
  terminals: ReadonlySet<string>,
  terminalsForState: (state: string, domainTerminals: ReadonlySet<string>) => ReadonlySet<string>,
): TraceReferenceGroup {
  return {
    terminals:
      reference.state === "unavailable" ? terminals : terminalsForState(reference.state, terminals),
    references: [reference.trace_id],
  };
}
