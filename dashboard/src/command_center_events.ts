import { textElement } from "./dom";
import { shortTime, stateLabel, statusClass } from "./format";
import type { AutonomousTaskReceipt, DirectedJobEvent } from "./schema";

export function eventHeading(label: string, state: string, timestamp: string): HTMLElement {
  const heading = document.createElement("header");
  const time = document.createElement("time");
  time.dateTime = timestamp;
  time.textContent = shortTime(timestamp);
  heading.append(
    textElement("strong", label),
    textElement("span", stateLabel(state), statusClass(state)),
    time,
  );
  return heading;
}

export function directedEventText(event: DirectedJobEvent): string {
  return `${event.kind.toUpperCase()} · ${event.step ?? event.summary ?? stateLabel(event.state)}`;
}

export function renderAutonomous(task: AutonomousTaskReceipt): HTMLElement {
  const article = document.createElement("article");
  article.className = "command-event command-event-autonomous";
  article.dataset["channel"] = "autonomous-job";
  article.append(
    eventHeading("자율 연구 작업", task.state, task.occurred_at),
    textElement("p", `${task.trigger_type} · ${task.kind}`, "command-request"),
  );
  if (task.summary !== null) article.append(textElement("p", task.summary, "command-response"));
  return article;
}
