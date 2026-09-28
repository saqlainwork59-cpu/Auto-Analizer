"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Shell } from "@/components/Shell";
import { StateBadge } from "@/components/ui";
import { useAuth } from "@/lib/providers";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (user === null) router.replace("/login");
  }, [user, router]);
  if (!user) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <StateBadge kind="ANALYZING" />
      </div>
    );
  }
  return <Shell>{children}</Shell>;
}
