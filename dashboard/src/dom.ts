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

class DomContractError extends Error {
  override readonly name = "DomContractError";
}
