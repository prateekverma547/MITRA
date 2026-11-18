// Track H. Screening: candidates on a job request, the CV check, the fit and
// the screening gate. Every signed-in role can read; only HR adds and decides.

import type { Feature } from "../../shell/registry";
import { AddCandidate } from "./AddCandidate";
import { CandidateList } from "./CandidateList";
import { CandidatePage } from "./CandidatePage";

export const feature: Feature = {
  routes: [
    { path: "/jobs/:jobId/candidates", element: <CandidateList /> },
    { path: "/jobs/:jobId/candidates/new", element: <AddCandidate />, roles: ["hr"] },
    { path: "/candidates/:candidateId", element: <CandidatePage /> },
  ],
};
