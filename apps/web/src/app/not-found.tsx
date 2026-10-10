import { Compass } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <EmptyState
      className="min-h-[60vh]"
      icon={<Compass aria-hidden />}
      title="Page not found"
      description="The page you're looking for doesn't exist or was moved."
      action={
        <Button asChild>
          <Link href="/">Back to overview</Link>
        </Button>
      }
    />
  );
}
