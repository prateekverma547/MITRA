// Track H. The two screens anyone can open: the welcome page and sign-in.

import type { Feature } from "../../shell/registry";
import { SignIn } from "./SignIn";
import { Welcome } from "./Welcome";

export const feature: Feature = {
  routes: [
    { path: "/", element: <Welcome />, public: true },
    { path: "/sign-in", element: <SignIn />, public: true },
  ],
};
