import { ApiError, getProject } from "@/lib/api";
import { useAsync, clearAsyncCache } from "@/hooks/useAsync";
import type { Project } from "@/types";
import {
  createContext,
  useCallback,
  useEffect,
  useContext,
  useMemo,
  useState,
  useRef,
  type ReactNode,
} from "react";

export type WorkspaceView = "control" | "subproject" | "knowledge" | "people";

interface WorkspaceState {
  project: Project | null;
  projectLoading: boolean;
  projectError: Error | null;
  accessRevision: number;
  refreshAccess: () => void;
  revalidateAccess: () => Promise<void>;
  activeProjectId: number | null;
  activeSubprojectId: number | null;
  activeProjectName: string | null;
  activeSubprojectName: string | null;
  setActiveProjectId: (id: number | null, name?: string | null) => void;
  setActiveSubprojectId: (id: number | null, name?: string | null) => void;
  activeTicketId: number | null;
  openTicket: (id: number | null) => void;
  view: WorkspaceView;
  setView: (view: WorkspaceView) => void;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [activeProjectId, setActiveProjectIdRaw] = useState<number | null>(
    Number(new URLSearchParams(window.location.search).get("project")) || null,
  );
  const [activeSubprojectId, setActiveSubprojectIdRaw] = useState<
    number | null
  >(null);
  const [activeProjectName, setActiveProjectName] = useState<string | null>(
    null,
  );
  const [activeSubprojectName, setActiveSubprojectName] = useState<
    string | null
  >(null);
  const [activeTicketId, setActiveTicketId] = useState<number | null>(null);
  const [view, setView] = useState<WorkspaceView>("control");

  const [accessRevision, setAccessRevision] = useState(0);
  const current = useAsync(
    () =>
      activeProjectId == null
        ? Promise.resolve(null)
        : getProject(activeProjectId),
    [activeProjectId, accessRevision],
  );
  const refreshAccess = useCallback(() => {
    clearAsyncCache();
    setActiveTicketId(null);
    setAccessRevision((n) => n + 1);
  }, []);

  const liveProjectId = useRef(activeProjectId);
  liveProjectId.current = activeProjectId;
  const validationRun = useRef(0);
  const revalidateAccess = useCallback(async () => {
    if (activeProjectId == null || !current.data) return;
    const run = ++validationRun.current;
    const stillCurrent = () =>
      run === validationRun.current &&
      liveProjectId.current === activeProjectId;
    try {
      const latest = await getProject(activeProjectId);
      if (
        stillCurrent() &&
        JSON.stringify(latest) !== JSON.stringify(current.data)
      )
        refreshAccess();
    } catch (error) {
      if (
        stillCurrent() &&
        error instanceof ApiError &&
        [401, 403, 404].includes(error.status)
      )
        refreshAccess();
      // A transient network failure does not discard drafts or one-time secrets.
    }
  }, [activeProjectId, current.data, refreshAccess]);

  const setActiveProjectId = useCallback(
    (id: number | null, name: string | null = null) => {
      const url = new URL(window.location.href);
      if (id == null) url.searchParams.delete("project");
      else url.searchParams.set("project", String(id));
      window.history.pushState({}, "", url);
      setActiveProjectIdRaw(id);
      setActiveProjectName(id == null ? null : name);
      // Switching project invalidates subproject/ticket context.
      setActiveSubprojectIdRaw(null);
      setActiveSubprojectName(null);
      setActiveTicketId(null);
      setView("control");
    },
    [],
  );

  const setActiveSubprojectId = useCallback(
    (id: number | null, name: string | null = null) => {
      setActiveSubprojectIdRaw(id);
      setActiveSubprojectName(id == null ? null : name);
      setActiveTicketId(null);
      if (id != null) setView("subproject");
    },
    [],
  );

  const openTicket = useCallback((id: number | null) => {
    setActiveTicketId(id);
  }, []);

  useEffect(() => {
    const onPop = () => {
      setActiveProjectIdRaw(
        Number(new URLSearchParams(window.location.search).get("project")) ||
          null,
      );
      setActiveSubprojectIdRaw(null);
      setActiveTicketId(null);
      setView("control");
      refreshAccess();
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [refreshAccess]);

  const value = useMemo(
    () => ({
      project:
        current.loading || current.error || current.data?.id !== activeProjectId
          ? null
          : current.data,
      projectLoading: current.loading,
      projectError: current.error,
      accessRevision,
      refreshAccess,
      revalidateAccess,
      activeProjectId,
      activeSubprojectId,
      activeProjectName,
      activeSubprojectName,
      setActiveProjectId,
      setActiveSubprojectId,
      activeTicketId,
      openTicket,
      view,
      setView,
    }),
    [
      current.data,
      current.loading,
      current.error,
      accessRevision,
      refreshAccess,
      revalidateAccess,
      activeProjectId,
      activeSubprojectId,
      activeProjectName,
      activeSubprojectName,
      activeTicketId,
      setActiveProjectId,
      setActiveSubprojectId,
      openTicket,
      view,
    ],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used inside <WorkspaceProvider>");
  }
  return ctx;
}
