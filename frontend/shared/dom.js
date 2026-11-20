// Build DOM elements without innerHTML. Text is always inserted as a text node, so nothing a
// candidate, employer or interviewer types can become markup.

/** Create an element. Props map to attributes or properties; children may be nested arrays. */
export function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "htmlFor") node.setAttribute("for", value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (typeof value === "boolean") {
      if (key in node) node[key] = value;
      else node.setAttribute(key, "");
    } else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

/** Show or hide an element with the `hidden` class the stylesheets already define. */
export function setHidden(node, hidden) {
  node.classList.toggle("hidden", Boolean(hidden));
}
