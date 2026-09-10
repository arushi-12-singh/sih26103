"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { isDemoAuthenticated } from "@/lib/demo-auth";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const authenticated = isDemoAuthenticated();
    if (pathname === "/login") {
      if (authenticated) router.replace("/portfolio");
      else {
        const timer = window.setTimeout(() => setReady(true), 0);
        return () => window.clearTimeout(timer);
      }
      return;
    }
    if (!authenticated) {
      router.replace("/login");
      return;
    }
    const timer = window.setTimeout(() => setReady(true), 0);
    return () => window.clearTimeout(timer);
  }, [pathname, router]);

  return ready ? children : null;
}
