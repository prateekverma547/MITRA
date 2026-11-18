import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "./Button";

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** A value someone needs to pass on, with a button that copies it. */
export function CopyField({ label, value, small = false }: { label: string; value: string; small?: boolean }) {
  const [done, setDone] = useState(false);
  return (
    <div className="copy-field">
      <span className="label">{label}</span>
      <div className="copy-row">
        <span className={small ? "copy-value small-value" : "copy-value"}>{value}</span>
        <Button
          size="sm"
          variant="ghost"
          icon={done ? <Check size={15} /> : <Copy size={15} />}
          onClick={async () => {
            if (await copy(value)) {
              setDone(true);
              window.setTimeout(() => setDone(false), 1600);
            }
          }}
          aria-label={`Copy ${label}`}
        >
          {done ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

export { copy as copyText };
