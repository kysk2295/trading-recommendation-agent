import { textElement } from "../dom";
import type { OptionsWorkbench } from "../options_workbench_schema";
import type { ChainLegSelection } from "./options_chain_table";
import { type StrategyLeg, scenarioSeries } from "./options_workbench_presenters";

export function renderWorkbenchHeader(): HTMLElement {
  const header = document.createElement("header");
  header.className = "options-workbench-header";
  const copy = document.createElement("div");
  copy.append(
    textElement("p", "OPTIONS RESEARCH", "meta-label"),
    textElement("h2", "Options Workbench"),
  );
  header.append(
    copy,
    textElement(
      "p",
      "RESEARCH ONLY · read-only provider evidence · no execution controls",
      "options-workbench-notice",
    ),
  );
  return header;
}

export function renderProviderStates(workbench: OptionsWorkbench): HTMLElement {
  const list = document.createElement("p");
  const labels = new Set<string>();
  for (const row of workbench.chain.rows) {
    if (row.call !== null) labels.add(`${row.call.provider} · ${row.call.state}`);
    if (row.put !== null) labels.add(`${row.put.provider} · ${row.put.state}`);
  }
  list.textContent = labels.size === 0 ? "Provider state unavailable" : [...labels].join(" · ");
  return list;
}

export function renderScenarioPanel(workbench: OptionsWorkbench): HTMLElement {
  const article = document.createElement("article");
  article.className = "options-workbench-scenario";
  updateScenarioPanel(article, workbench, null);
  return article;
}

export function updateScenarioPanel(
  article: HTMLElement,
  workbench: OptionsWorkbench,
  selection: ChainLegSelection | null,
): void {
  article.replaceChildren(...scenarioContent(workbench, selection));
}

function scenarioContent(
  workbench: OptionsWorkbench,
  selection: ChainLegSelection | null,
): readonly Node[] {
  if (workbench.scenario === null) {
    return [
      textElement("h3", "Scenario unavailable"),
      textElement("p", "No canonical scenario receipt"),
    ];
  }
  const legs: readonly StrategyLeg[] =
    selection === null
      ? workbench.scenario.legs.map((leg) => ({
          ...leg,
          strike: Number(leg.strike),
          premium: Number(leg.premium),
        }))
      : [
          {
            action: "long",
            side: selection.side,
            strike: selection.strike,
            premium: selection.premium,
            quantity: 1,
            multiplier: 100,
          },
        ];
  const contractId =
    selection?.contractId ?? workbench.scenario.legs[0]?.contract_id ?? "unavailable";
  const list = document.createElement("ol");
  list.dataset["scenarioSeries"] = "true";
  for (const point of scenarioSeries(legs, workbench.scenario.scenario_spots.map(Number))) {
    const item = document.createElement("li");
    item.append(
      textElement("span", point.spot.toFixed(2)),
      textElement("strong", point.payoff.toFixed(2)),
    );
    list.append(item);
  }
  const selected = textElement("p", contractId);
  selected.dataset["selectedContract"] = "true";
  return [
    textElement("h3", "Deterministic expiration scenario"),
    selected,
    list,
    textElement("p", "RESEARCH ONLY · immutable snapshot input"),
  ];
}
