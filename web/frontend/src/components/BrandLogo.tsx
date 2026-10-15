import * as React from "react";
import { cn } from "@/lib/utils";

export function BrandLogo({
  className,
  glow = true,
}: {
  className?: string;
  glow?: boolean;
}) {
  const [failed, setFailed] = React.useState(false);

  return (
    <div
      className={cn(
        "relative flex shrink-0 items-center justify-center overflow-hidden rounded-xl ring-1 ring-border",
        glow && "shadow-sm shadow-primary/20",
        className,
      )}
    >
      {failed ? (
        <span className="flex h-full w-full items-center justify-center bg-gradient-to-br from-primary to-blue-700 text-[13px] font-bold text-primary-foreground">
          N
        </span>
      ) : (
        <img
          src="/logo.jpg"
          alt="NEMESIS"
          loading="eager"
          decoding="async"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      )}
    </div>
  );
}