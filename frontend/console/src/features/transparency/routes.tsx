// Track H, phase 4. Transparency for the org admin: decisions beside the
// model's recommendation, the records behind them, the AI usage register and
// the audit trail. Read-only; admins read, people in HR and the BUs decide.

import { ShieldCheck } from "lucide-react";
import type { Feature } from "../../shell/registry";
import { AuditTrail } from "./AuditTrail";
import { Calls } from "./Calls";
import { Costs } from "./Costs";
import { DecisionLog } from "./DecisionLog";
import { Records } from "./Records";
import { Register } from "./Register";

const ADMIN = ["org_admin"] as const;

export const feature: Feature = {
  routes: [
    { path: "/transparency", element: <DecisionLog />, roles: [...ADMIN] },
    { path: "/transparency/records", element: <Records />, roles: [...ADMIN] },
    { path: "/transparency/ai", element: <Register />, roles: [...ADMIN] },
    { path: "/transparency/audit", element: <AuditTrail />, roles: [...ADMIN] },
    { path: "/transparency/calls", element: <Calls />, roles: [...ADMIN] },
    { path: "/transparency/cost", element: <Costs />, roles: [...ADMIN] },
  ],
  nav: [{ to: "/transparency", label: "Transparency", roles: [...ADMIN], order: 120, icon: ShieldCheck, section: "Organisation" }],
};
