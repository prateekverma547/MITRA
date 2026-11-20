// Light or dark, chosen by the person and remembered in this browser. Light is
// the default. index.html applies it before the first paint.

import { Moon, Sun } from "lucide-react";
import { useState } from "react";

type Theme = "light" | "dark";
const KEY = "mitra.theme";

function current(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(current);

  function choose(next: Theme) {
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // Storage can be unavailable; the choice still applies to this page.
    }
  }

  return (
    <span className="theme-toggle" role="group" aria-label="Colour theme">
      <button type="button" aria-pressed={theme === "light"} aria-label="Light theme" title="Light" onClick={() => choose("light")}>
        <Sun size={15} />
      </button>
      <button type="button" aria-pressed={theme === "dark"} aria-label="Dark theme" title="Dark" onClick={() => choose("dark")}>
        <Moon size={15} />
      </button>
    </span>
  );
}
