// A temporary password, shown once. MITRA sends no email (decided 2026-11-18),
// so the admin passes it on; after this panel closes nobody can see it again.

import { KeyRound, X } from "lucide-react";
import { Button } from "../../shell/ui/Button";
import { CopyField } from "../../shell/ui/CopyField";

export function OneTimePassword({ who, email, password, onClose }: { who: string; email: string; password: string; onClose: () => void }) {
  return (
    <section className="once" aria-live="polite">
      <div className="once-head">
        <div>
          <p className="once-title">
            <KeyRound size={16} aria-hidden="true" /> Temporary password for {who}
          </p>
          <p className="muted small">
            Shown once. Copy it now and give it to {who} yourself; MITRA does not send email. They sign in with {email}.
          </p>
        </div>
        <Button variant="ghost" size="sm" icon={<X size={15} />} onClick={onClose}>
          Done
        </Button>
      </div>
      <CopyField label="Password" value={password} />
    </section>
  );
}
