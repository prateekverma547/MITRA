// Who is signed in. Asked of the server once on load and after sign-in; the
// role always comes from the server's answer, never from the door someone
// clicked on the welcome screen.

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import type { Me } from "./contracts";

interface Session {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<Me>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<Me>("/auth/me")
      .then(setMe)
      .catch((error) => {
        if (!(error instanceof ApiError && error.status === 401)) console.error(error);
        setMe(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const signedIn = await api.post<Me>("/auth/login", { email, password });
    setMe(signedIn);
    return signedIn;
  }, []);

  const signOut = useCallback(async () => {
    await api.post("/auth/logout");
    setMe(null);
  }, []);

  return (
    <SessionContext.Provider value={{ me, loading, signIn, signOut }}>{children}</SessionContext.Provider>
  );
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}
