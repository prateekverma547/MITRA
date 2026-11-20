// Track H. The candidate's interview room, at /meet (which redirects here).
// It talks to Track I's candidate endpoints, /interviews/join and
// /interviews/focus, and to /api/room/lookup for what consent must describe.

import type { Feature } from "../../shell/registry";
import { MeetPage } from "./MeetPage";

export const feature: Feature = {
  routes: [{ path: "/meet", element: <MeetPage />, open: true }],
};
