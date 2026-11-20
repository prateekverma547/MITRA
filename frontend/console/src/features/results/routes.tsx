// Track H. What the BU head reads and decides on, every candidate on one job
// side by side, and the live room for the MITRA interview round. HR and admins
// read all three; only the BU head decides.

import type { Feature } from "../../shell/registry";
import { Compare } from "./Compare";
import { ResultsPage } from "./ResultsPage";
import { TechnicalRoom } from "./TechnicalRoom";

export const feature: Feature = {
  routes: [
    { path: "/candidates/:candidateId/results", element: <ResultsPage /> },
    { path: "/jobs/:jobId/compare", element: <Compare /> },
    { path: "/candidates/:candidateId/technical-room", element: <TechnicalRoom /> },
  ],
};
