import { useEffect, useRef } from "react";
import {
  clearAsyncCache,
  invalidateAsyncCache,
  invalidateAsyncCachePrefix,
} from "@/hooks/useAsync";
import { apiBase } from "@/lib/api";
import type { SSEPayload } from "@/types";

function invalidateCachedReads(payload: SSEPayload): void {
  if (
    payload.action === "SYNC_REQUIRED" ||
    payload.action === "PROJECT_ACCESS_CHANGED"
  ) {
    clearAsyncCache();
    return;
  }
  if (payload.entity === "project") {
    invalidateAsyncCache("projects");
    invalidateAsyncCache(`control-room:${payload.entity_id}`);
    invalidateAsyncCache(`projects:${payload.entity_id}:subprojects`);
    return;
  }
  if (payload.entity === "subproject") {
    invalidateAsyncCache(`subproject:${payload.entity_id}`);
    if (payload.parent_id != null) {
      invalidateAsyncCache(`projects:${payload.parent_id}:subprojects`);
      invalidateAsyncCache(`control-room:${payload.parent_id}`);
    }
    return;
  }
  if (payload.entity === "ticket") {
    if (payload.parent_id != null) {
      invalidateAsyncCache(`subproject:${payload.parent_id}`);
    }
    // Ticket invalidations name their subproject, not their project. Clear
    // the small family of warm summaries so an unmounted project cannot hide
    // an agent update until its TTL expires.
    invalidateAsyncCachePrefix("control-room:");
    return;
  }
  if (
    payload.parent_id != null &&
    (payload.entity === "knowledge_node" ||
      payload.entity === "knowledge_proposal" ||
      payload.entity === "agent_session")
  ) {
    invalidateAsyncCache(`control-room:${payload.parent_id}`);
  }
}

/**
 * Subscribe to the backend SSE stream. Dispatches every message to the
 * provided handler. A single EventSource is shared across the component
 * tree — the handler updates on every render but the socket does not
 * reconnect unless the component remounts.
 *
 * See `docs/client_server.md` for the refetch lifecycle.
 */
export function useSSE(onEvent: (payload: SSEPayload) => void): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const source = new EventSource(`${apiBase}/events`, {
      withCredentials: true,
    });

    const dispatch = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as SSEPayload;
        invalidateCachedReads(payload);
        handlerRef.current(payload);
      } catch (err) {
        console.error("SSE payload parse failed", err, event.data);
      }
    };
    source.onmessage = dispatch;
    source.addEventListener("ready", dispatch as EventListener);
    source.addEventListener("resync", dispatch as EventListener);

    source.onerror = (event) => {
      // EventSource auto-reconnects; surface transient errors for debugging.
      console.warn("SSE connection hiccup", event);
    };

    const revalidate = () => {
      if (document.visibilityState !== "visible") return;
      clearAsyncCache();
      handlerRef.current({
        action: "SYNC_REQUIRED",
        entity: "project",
        entity_id: 0,
        parent_id: null,
        workspace_id: null,
      });
    };
    window.addEventListener("focus", revalidate);
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      window.removeEventListener("focus", revalidate);
      document.removeEventListener("visibilitychange", revalidate);
      source.removeEventListener("ready", dispatch as EventListener);
      source.removeEventListener("resync", dispatch as EventListener);
      source.close();
    };
  }, []);
}
