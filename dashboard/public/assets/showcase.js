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
  drawer.showModal();
});
