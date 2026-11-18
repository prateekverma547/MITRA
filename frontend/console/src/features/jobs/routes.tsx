// Track H. Job requests: the BU head describes a role until MITRA understands
// it, then sends it to HR. HR and admins can open an approved request read-only.

import { Briefcase } from "lucide-react";
import type { Feature } from "../../shell/registry";
import { JobDetail } from "./JobDetail";
import { JobList } from "./JobList";
import { NewJob } from "./NewJob";

export const feature: Feature = {
  routes: [
    { path: "/jobs", element: <JobList /> },
    { path: "/jobs/new", element: <NewJob />, roles: ["bu_head"] },
    { path: "/jobs/:jobId", element: <JobDetail /> },
  ],
  nav: [{ to: "/jobs", label: "Job requests", roles: ["bu_head", "hr", "org_admin"], order: 10, icon: Briefcase }],
};
