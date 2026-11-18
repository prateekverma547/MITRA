// Track H. Every candidate, across jobs and business units.

import type { Feature } from "../../shell/registry";
import { Directory } from "./Directory";

export const feature: Feature = {
  routes: [{ path: "/candidates", element: <Directory /> }],
  nav: [{ to: "/candidates", label: "Candidates", roles: ["hr", "bu_head", "org_admin"], order: 20 }],
};
