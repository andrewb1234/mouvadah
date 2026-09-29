import { ApiError } from "@/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

interface AsyncState<T> {
  data: T | undefined;
  loading: boolean;
  error: Error | null;
}

interface CachedQuery<T> {
  data: T | undefined;
  error: Error | null;
  updatedAt: number;
  inFlight: Promise<T> | null;
  inFlightForced: boolean;
  inFlightStarted: boolean;
  generation: number;
}

const queryCache = new Map<string, CachedQuery<unknown>>();
const DEFAULT_CACHE_STALE_MS = 30_000;

function readCache<T>(key: string): CachedQuery<T> | undefined {
  return queryCache.get(key) as CachedQuery<T> | undefined;
}

function fetchCached<T>(
  key: string,
  fetcher: () => Promise<T>,
  force: boolean,
  staleTimeMs: number,
): Promise<T> {
  let entry = readCache<T>(key);
  if (!entry) {
    entry = {
      data: undefined,
      error: null,
      updatedAt: 0,
      inFlight: null,
      inFlightForced: false,
      inFlightStarted: false,
      generation: 0,
    };
    queryCache.set(key, entry);
  }

  if (
    !force &&
    entry.data !== undefined &&
    Date.now() - entry.updatedAt < staleTimeMs
  ) {
    return Promise.resolve(entry.data);
  }
  if (entry.inFlight) {
    if (!force) return entry.inFlight;
    if (entry.inFlightForced) {
      // Multiple mounted consumers can react to the same invalidation before
      // the request starts, so they may safely share it. Once the request has
      // started, a later invalidation may describe a newer committed state;
      // queue one trailing read instead of swallowing that invalidation.
      if (!entry.inFlightStarted) return entry.inFlight;
      return entry.inFlight.then(
        () => fetchCached(key, fetcher, true, staleTimeMs),
        () => fetchCached(key, fetcher, true, staleTimeMs),
      );
    }
    // A forced request supersedes an initial/stale read that may have started
    // before an SSE invalidation. The generation guard below prevents the
    // older response from replacing the newer cache entry.
  }

  const generation = ++entry.generation;

  const request = Promise.resolve()
    .then(() => {
      if (entry!.inFlight === request) entry!.inFlightStarted = true;
      return fetcher();
    })
    .then((data) => {
      if (entry!.generation === generation) {
        entry!.data = data;
        entry!.error = null;
        entry!.updatedAt = Date.now();
      }
      return data;
    })
    .catch((error: unknown) => {
      const normalized =
        error instanceof Error ? error : new Error(String(error));
      if (entry!.generation === generation) entry!.error = normalized;
      throw normalized;
    })
    .finally(() => {
      if (entry!.inFlight === request) {
        entry!.inFlight = null;
        entry!.inFlightForced = false;
        entry!.inFlightStarted = false;
      }
    });
  entry.inFlight = request;
  entry.inFlightForced = force;
  entry.inFlightStarted = false;
  return request;
}

/** Drop a cached query before its next consumer reads it. */
export function invalidateAsyncCache(key: string): void {
  queryCache.delete(key);
}

/** Drop a cache family when an invalidation cannot name every affected key. */
export function invalidateAsyncCachePrefix(prefix: string): void {
  for (const key of queryCache.keys()) {
    if (key.startsWith(prefix)) queryCache.delete(key);
  }
}

/** Drop all identity-bound data when the authenticated principal changes. */
export function clearAsyncCache(): void {
  queryCache.clear();
}

/**
 * Minimal async-data hook with manual refetch and cache-friendly dependency
 * tracking. We bake our own instead of pulling SWR so the bundle stays tiny
 * and the SSE invalidation flow stays explicit.
 */
export function useAsync<T>(
  fetcher: () => Promise<T>,
  deps: ReadonlyArray<unknown>,
  options: {
    cacheKey?: string;
    staleTimeMs?: number;
    retainOnError?: boolean;
  } = {},
): AsyncState<T> & { refetch: () => void } {
  const [state, setState] = useState<AsyncState<T>>({
    data: undefined,
    loading: true,
    error: null,
  });

  const latestRunId = useRef(0);

  const load = useCallback(
    (force: boolean) => {
      const runId = ++latestRunId.current;
      const cached = options.cacheKey
        ? readCache<T>(options.cacheKey)
        : undefined;
      setState((prev) => ({
        data: cached?.data ?? prev.data,
        loading: true,
        error: null,
      }));
      const request = options.cacheKey
        ? fetchCached(
            options.cacheKey,
            fetcher,
            force,
            options.staleTimeMs ?? DEFAULT_CACHE_STALE_MS,
          )
        : Promise.resolve().then(fetcher);
      request
        .then((data) => {
          if (runId !== latestRunId.current) return;
          setState({ data, loading: false, error: null });
        })
        .catch((err: unknown) => {
          if (runId !== latestRunId.current) return;
          setState((previous) => ({
            ...previous,
            data:
              options.retainOnError &&
              !(err instanceof ApiError && [401, 403].includes(err.status))
                ? previous.data
                : undefined,
            loading: false,
            error: err instanceof Error ? err : new Error(String(err)),
          }));
        });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    },
    [options.cacheKey, options.staleTimeMs, options.retainOnError, ...deps],
  );

  const refetch = useCallback(() => {
    load(true);
  }, [load]);

  useEffect(() => {
    load(false);
    return () => {
      latestRunId.current++;
    };
  }, [load]);

  return { ...state, refetch };
}
