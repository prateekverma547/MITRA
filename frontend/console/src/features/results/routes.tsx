// Track H. What the BU head reads and decides on, and every candidate on one
// job side by side. HR and admins read both; only the BU head decides.

import type { Feature } from "../../shell/registry";
import { Compare } from "./Compare";
import { ResultsPage } from "./ResultsPage";

export const feature: Feature = {
  routes: [
    { path: "/candidates/:candidateId/results", element: <ResultsPage /> },
    { path: "/jobs/:jobId/compare", element: <Compare /> },
  ],
};
