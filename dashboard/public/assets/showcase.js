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
let traceInvoker = null;

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
