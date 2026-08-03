const rows = document.querySelector("#stress-rows");
const states = ["populated", "stale", "blocked", "unavailable"];

rows.innerHTML = Array.from({ length: 200 }, (_, index) => {
  const number = String(index + 1).padStart(3, "0");
  const state = states[index % states.length];
  return `<tr><td><code>DEMO-EVIDENCE-${number}-LONG-IDENTIFIER-7F9A4B2C1D</code></td><td>${state}</td><td>2026-07-26T00:00Z</td><td>긴 한국어와 English demonstration summary ${number}</td><td><button class="trace-trigger" type="button" data-trace-state="${state}">Trace</button></td></tr>`;
}).join("");

const drawer = document.querySelector(".trace-drawer");
const state = document.querySelector("#trace-state");
const summary = document.querySelector("#trace-summary");
const terminal = document.querySelector("#trace-terminal");
const traceTitle = document.querySelector("#trace-title");
const workbenchTablist = document.querySelector(".options-workbench-tabs");
const workbenchTabs = [...document.querySelectorAll('.options-workbench-tabs [role="tab"]')];
let traceInvoker = null;

function activateWorkbenchTab(nextTab) {
  for (const tab of workbenchTabs) {
    const isSelected = tab === nextTab;
    const panel = document.querySelector(`#${tab.getAttribute("aria-controls")}`);
    tab.setAttribute("aria-selected", String(isSelected));
    tab.tabIndex = isSelected ? 0 : -1;
    panel.hidden = !isSelected;
  }
  nextTab.focus();
}

function moveWorkbenchTab(event) {
  const currentIndex = workbenchTabs.indexOf(event.target);
  if (currentIndex === -1) return;

  const lastIndex = workbenchTabs.length - 1;
  let nextIndex = currentIndex;
  switch (event.key) {
    case "ArrowLeft":
      nextIndex = currentIndex === 0 ? lastIndex : currentIndex - 1;
      break;
    case "ArrowRight":
      nextIndex = currentIndex === lastIndex ? 0 : currentIndex + 1;
      break;
    case "Home":
      nextIndex = 0;
      break;
    case "End":
      nextIndex = lastIndex;
      break;
    case "Enter":
    case " ":
      break;
    default:
      return;
  }

  event.preventDefault();
  activateWorkbenchTab(workbenchTabs[nextIndex]);
}

workbenchTablist.addEventListener("click", (event) => {
  const tab = event.target.closest('[role="tab"]');
  if (tab) activateWorkbenchTab(tab);
});
workbenchTablist.addEventListener("keydown", moveWorkbenchTab);

function focusTraceControl(event) {
  if (event.key !== "Tab") return;

  const controls = [
    ...drawer.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ];
  if (controls.length === 0) {
    event.preventDefault();
    return;
  }

  const first = controls[0];
  const last = controls.at(-1);
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !controls.includes(active))) {
    event.preventDefault();
    last.focus();
  }
  if (!event.shiftKey && (active === last || !controls.includes(active))) {
    event.preventDefault();
    first.focus();
  }
}

drawer.addEventListener("keydown", focusTraceControl);
drawer.addEventListener("cancel", (event) => {
  event.preventDefault();
  drawer.close();
});
drawer.addEventListener("close", () => {
  traceInvoker?.focus();
  traceInvoker = null;
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest(".trace-trigger");
  if (!trigger) return;

  const value = trigger.dataset.traceState;
  state.textContent = value;
  summary.textContent = ["blocked", "corrupt", "unavailable"].includes(value)
    ? "This trace ends at an explicit blocker terminal."
    : "This ordered evidence list ends in a bounded terminal decision.";
  terminal.textContent = ["blocked", "corrupt", "unavailable"].includes(value)
    ? "Blocker terminal"
    : "Reviewer decision";
  traceInvoker = trigger;
  drawer.showModal();
  traceTitle.focus();
});
