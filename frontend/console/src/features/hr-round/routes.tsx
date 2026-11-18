// Track H. The HR round: booked from the candidate page, run from here.

import type { Feature } from "../../shell/registry";
import { HRRoundPage } from "./HRRoundPage";

export const feature: Feature = {
  routes: [{ path: "/candidates/:candidateId/hr-round", element: <HRRoundPage /> }],
};
