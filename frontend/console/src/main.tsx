import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/newsreader/opsz.css";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource-variable/instrument-sans/index.css";
import { App } from "./App";
import "./shell/shell.css";

// Page transitions slide forward unless a Back button said otherwise. Browser
// back and forward are marked too, so the slide follows the direction of travel.
window.addEventListener("popstate", () => (document.documentElement.dataset.nav = "back"));
document.addEventListener("click", (event) => {
  if (!(event.target as HTMLElement).closest(".back")) document.documentElement.dataset.nav = "forward";
}, true);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
