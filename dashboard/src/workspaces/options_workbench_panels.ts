import { textElement } from "../dom";

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
