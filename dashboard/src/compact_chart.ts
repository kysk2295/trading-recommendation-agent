import { textElement } from "./dom";
import type { WorkspaceItem } from "./render";

export function renderCompactChart(items: readonly WorkspaceItem[]): HTMLElement | null {
  const points = items.flatMap((item, index) => {
    if (item.value === null) return [];
    const value = Number.parseFloat(item.value);
    return Number.isFinite(value) ? [{ index, value, label: item.label }] : [];
  });
  if (points.length < 2) return null;
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const range = Math.max(...values) - min || 1;
  const coordinates = points
    .map((point, index) => {
      const x = (index / (points.length - 1)) * 100;
      const y = 36 - ((point.value - min) / range) * 32;
      return `${x},${y}`;
    })
    .join(" ");
  const figure = document.createElement("figure");
  figure.className = "compact-chart";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 100 40");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "표에 표시된 값의 상대 추세");
  const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  polyline.setAttribute("points", coordinates);
  svg.append(polyline);
  figure.append(svg, textElement("figcaption", "상대 추세 · 정확한 값은 아래 표 참조"));
  return figure;
}
