// Track H. The org admin's own screens: people and business units, and
// privacy (deletion requests, retention periods, consent records).

import { ShieldCheck, UserCog } from "lucide-react";
import type { Feature } from "../../shell/registry";
import { People } from "./People";
import { Privacy } from "./Privacy";

export const feature: Feature = {
  routes: [
    { path: "/admin/people", element: <People />, roles: ["org_admin"] },
    { path: "/admin/privacy", element: <Privacy />, roles: ["org_admin"] },
  ],
  nav: [
    { to: "/admin/people", label: "People", roles: ["org_admin"], order: 100, icon: UserCog, section: "Organisation" },
    { to: "/admin/privacy", label: "Privacy", roles: ["org_admin"], order: 110, icon: ShieldCheck, section: "Organisation" },
  ],
};
