"use client";

import { CircleAlert, VideoOff, WifiOff } from "lucide-react";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { Spinner } from "@/components/ui/spinner";
import { useI18n } from "@/i18n/provider";
import { snapshotUrl, streamUrl, type CameraStatus } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/**
 * Browsers allow ~6 concurrent HTTP/1.1 connections per origin. MJPEG streams hold a
 * connection open, so only a few visible feeds stream at once; the rest refresh snapshots.
 */
const MAX_STREAMS = 4;
const SNAPSHOT_REFRESH_MS = 1_000;
// After a stream drops (API restart, network blip) snapshots fill in, then the stream is tried
// again: soon at first, less often while it keeps failing.
const RETRY_FIRST_MS = 2_000;
const RETRY_MAX_MS = 30_000;

class StreamSlots {
  private holders: string[] = [];
  private listeners = new Set<() => void>();
  private version = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = () => this.version;

  request(key: string, priority: boolean) {
    if (this.holders.includes(key)) return;
    this.holders = priority ? [key, ...this.holders] : [...this.holders, key];
    this.emit();
  }

  release(key: string) {
    if (!this.holders.includes(key)) return;
    this.holders = this.holders.filter((holder) => holder !== key);
    this.emit();
  }

  granted(key: string) {
    const index = this.holders.indexOf(key);
    return index >= 0 && index < MAX_STREAMS;
  }

  private emit() {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }
}

const StreamSlotsContext = createContext<StreamSlots | null>(null);

export function StreamSlotsProvider({ children }: { children: ReactNode }) {
  const [slots] = useState(() => new StreamSlots());
  return <StreamSlotsContext value={slots}>{children}</StreamSlotsContext>;
}

function useStreamSlot(active: boolean, priority: boolean) {
  const slots = useContext(StreamSlotsContext);
  const key = useId();
  useSyncExternalStore(
    slots?.subscribe ?? (() => () => undefined),
    slots?.getVersion ?? (() => 0),
    () => 0,
  );
  useEffect(() => {
    if (!slots || !active) return;
    slots.request(key, priority);
    return () => slots.release(key);
  }, [slots, key, active, priority]);
  return Boolean(slots && active && slots.granted(key));
}

function useIsVisible(ref: React.RefObject<HTMLElement | null>) {
  const [inView, setInView] = useState(false);
  const pageVisible = useSyncExternalStore(
    (callback) => {
      document.addEventListener("visibilitychange", callback);
      return () => document.removeEventListener("visibilitychange", callback);
    },
    () => document.visibilityState === "visible",
    () => false,
  );
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new IntersectionObserver(([entry]) => setInView(entry?.isIntersecting ?? false), {
      rootMargin: "100px",
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return inView && pageVisible;
}

function SnapshotPoller({ cameraId, alt, className }: { cameraId: number; alt: string; className?: string }) {
  const [nonce, setNonce] = useState(0);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const scheduleNext = () => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setNonce((value) => value + 1), SNAPSHOT_REFRESH_MS);
  };
  return (
    <img
      src={snapshotUrl(cameraId, { nonce })}
      alt={alt}
      className={className}
      onLoad={scheduleNext}
      onError={scheduleNext}
      draggable={false}
    />
  );
}

function MjpegStream({
  cameraId,
  attempt,
  alt,
  className,
  onFrame,
  onFailure,
}: {
  cameraId: number;
  attempt: number;
  alt: string;
  className?: string;
  onFrame: () => void;
  onFailure: () => void;
}) {
  const ref = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const image = ref.current;
    // Clearing src aborts the long-lived request immediately when the feed unmounts.
    return () => {
      if (image) image.src = "";
    };
  }, []);
  return (
    <img
      ref={ref}
      src={streamUrl(cameraId, attempt)}
      alt={alt}
      className={className}
      onLoad={onFrame}
      onError={onFailure}
      draggable={false}
    />
  );
}

/** Stream state with automatic reconnection: a failed stream is retried with backoff. */
function useStreamRetry(online: boolean) {
  const [failures, setFailures] = useState(0);
  const [waiting, setWaiting] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!waiting) return;
    const delay = Math.min(RETRY_FIRST_MS * 2 ** Math.max(0, failures - 1), RETRY_MAX_MS);
    const timer = setTimeout(() => {
      setWaiting(false);
      setAttempt((value) => value + 1);
    }, delay);
    return () => clearTimeout(timer);
  }, [waiting, failures]);

  // A camera coming back online is worth an immediate try.
  const [wasOnline, setWasOnline] = useState(online);
  if (online !== wasOnline) {
    setWasOnline(online);
    if (online) {
      setWaiting(false);
      setFailures(0);
      setAttempt((value) => value + 1);
    }
  }

  return {
    waiting,
    attempt,
    onFrame: () => setFailures(0),
    onFailure: () => {
      setFailures((value) => value + 1);
      setWaiting(true);
    },
  };
}

const OVERLAY_ICONS: Partial<Record<CameraStatus, ReactNode>> = {
  connecting: <Spinner className="size-5" />,
  offline: <WifiOff className="size-5" />,
  error: <CircleAlert className="size-5" />,
  disabled: <VideoOff className="size-5" />,
};

type LiveFeedProps = {
  cameraId: number;
  name: string;
  status: CameraStatus;
  /** Detail views get a stream slot ahead of grid tiles. */
  priority?: boolean;
  className?: string;
  children?: ReactNode;
};

/** Annotated live feed: MJPEG when a stream slot is free, snapshot refresh otherwise. */
export function LiveFeed({ cameraId, name, status, priority = false, className, children }: LiveFeedProps) {
  const container = useRef<HTMLDivElement>(null);
  const visible = useIsVisible(container);
  const online = status === "online";
  const stream = useStreamRetry(online);
  const granted = useStreamSlot(visible && online && !stream.waiting, priority);
  const { t } = useI18n();
  const overlay = status === "online" ? null : { icon: OVERLAY_ICONS[status], label: t.feed[status] };
  const alt = t.feed.alt(name);
  const imageClass = "absolute inset-0 size-full object-contain";

  return (
    <div ref={container} className={cn("feed-frame", className)}>
      {online && visible ? (
        granted ? (
          <MjpegStream
            key={stream.attempt}
            cameraId={cameraId}
            attempt={stream.attempt}
            alt={alt}
            className={imageClass}
            onFrame={stream.onFrame}
            onFailure={stream.onFailure}
          />
        ) : (
          <SnapshotPoller cameraId={cameraId} alt={alt} className={imageClass} />
        )
      ) : null}
      {overlay ? (
        <div className="text-muted-foreground absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm">
          {overlay.icon}
          <span>{overlay.label}</span>
        </div>
      ) : null}
      {children}
    </div>
  );
}
