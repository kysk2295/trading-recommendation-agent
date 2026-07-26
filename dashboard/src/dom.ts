type ElementConstructor<T extends Element> = {
  new (): T;
};

export function requiredElement<T extends Element>(
  id: string,
  elementType: ElementConstructor<T>,
): T {
  const element = document.getElementById(id);
  if (!(element instanceof elementType)) {
    throw new DomContractError(`missing or invalid element: ${id}`);
  }
  return element;
}

export function textElement(tag: string, text: string, className?: string): HTMLElement {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className !== undefined) {
    element.className = className;
  }
  return element;
}

export function buttonElement(label: string, className: string): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  return button;
}

export function timeElement(value: string | null): HTMLTimeElement {
  const time = document.createElement("time");
  if (value === null) {
    time.textContent = "관측 시각 없음";
    return time;
  }
  time.dateTime = value;
  time.textContent = new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
  return time;
}

export class DomContractError extends Error {
  override readonly name = "DomContractError";
}
