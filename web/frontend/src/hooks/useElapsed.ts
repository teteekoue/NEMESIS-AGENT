import * as React from "react";

/**
 * Returns the number of seconds elapsed since `startedAt` (epoch seconds),
 * ticking in real time while `active` is true.
 */
export function useElapsed(startedAt?: number | null, active = true): number {
  const [now, setNow] = React.useState(() => Date.now() / 1000);

  React.useEffect(() => {
    if (!active || !startedAt) return;
    setNow(Date.now() / 1000);
    const id = window.setInterval(() => setNow(Date.now() / 1000), 100);
    return () => window.clearInterval(id);
  }, [active, startedAt]);

  if (!startedAt) return 0;
  return Math.max(0, now - startedAt);
}