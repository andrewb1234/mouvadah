import { PeoplePage } from "@/components/PeoplePage";
import { useEffect } from "react";
import { Gauge, KanbanSquare, Loader2, Network, Users } from "lucide-react";
import { useAsync } from "@/hooks/useAsync";
import { useWorkspace } from "@/context/WorkspaceContext";
import { getSubproject } from "@/lib/api";
import type { SSEPayload, SubprojectDetail } from "@/types";
import { SubprojectHeader } from "@/components/SubprojectHeader";
import { KanbanBoard } from "@/components/KanbanBoard";
import { KnowledgePanel } from "@/components/KnowledgePanel";
import { ControlRoom } from "@/components/ControlRoom";
import { ResizableSplit } from "@/components/ui/resizable-split";
import { cn } from "@/lib/utils";

interface Props {
  lastEvent: SSEPayload | null;
}

export function Workspace({ lastEvent }: Props) {
  const {
    project,
    projectLoading,
    projectError,
    accessRevision,
    activeProjectId,
    activeSubprojectId,
    activeProjectName,
    activeSubprojectName,
    openTicket,
    view,
    setView,
  } = useWorkspace();
  const subproject = useAsync<SubprojectDetail | null>(
    () =>
      activeSubprojectId == null
        ? Promise.resolve(null)
        : getSubproject(activeSubprojectId),
    [activeSubprojectId],
    {
      cacheKey:
        activeSubprojectId == null
          ? "subproject:none"
          : `subproject:${activeSubprojectId}`,
    },
  );

  // SSE-driven targeted refetch. TICKET_DELETED/SUBPROJECT_DELETED fall out
  // of the same entity-scoped check so we don't need a special case — if
  // the active subproject vanishes, ``getSubproject`` will 404 and the
  // Sidebar SSE handler will clear the selection.
  useEffect(() => {
    if (!lastEvent || activeSubprojectId == null) return;
    if (
      lastEvent.action === "SYNC_REQUIRED" ||
      (lastEvent.entity === "ticket" &&
        lastEvent.parent_id === activeSubprojectId) ||
      (lastEvent.entity === "subproject" &&
        lastEvent.entity_id === activeSubprojectId)
    ) {
      subproject.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent, activeSubprojectId]);

  return (
    <main
      id="workspace-main"
      tabIndex={-1}
      className="flex min-w-0 flex-1 flex-col overflow-hidden"
    >
      <header className="shrink-0 border-b border-border bg-card/50 px-3 py-3 sm:px-5">
        <div className="mb-3 flex min-w-0 items-center gap-2">
          <span className="technical-label shrink-0">Active context</span>
          <span
            className="min-w-0 truncate text-sm font-semibold"
            title={activeProjectName ?? undefined}
          >
            {activeProjectId == null
              ? "No project selected"
              : (project?.name ??
                activeProjectName ??
                `Project #${activeProjectId}`)}
          </span>
          {activeSubprojectId != null && (
            <>
              <span className="text-muted-foreground" aria-hidden>
                /
              </span>
              <span
                className="min-w-0 truncate text-sm text-muted-foreground"
                title={activeSubprojectName ?? undefined}
              >
                {activeSubprojectName ?? `Subproject #${activeSubprojectId}`}
              </span>
            </>
          )}
        </div>
        <nav
          className="grid grid-cols-4 gap-1 sm:gap-2"
          aria-label="Workspace views"
        >
          <ViewTab
            active={view === "control"}
            onClick={() => setView("control")}
            icon={<Gauge className="h-4 w-4" />}
            label="Control Room"
            description="Project state and attention"
          />
          <ViewTab
            active={view === "knowledge"}
            onClick={() => setView("knowledge")}
            icon={<Network className="h-4 w-4" />}
            label="Knowledge"
            description="Plan and review evidence"
          />
          <ViewTab
            active={view === "subproject"}
            onClick={() => setView("subproject")}
            icon={<KanbanSquare className="h-4 w-4" />}
            label="Kanban"
            description={
              activeSubprojectId == null
                ? "Choose a subproject"
                : "Execute and hand off work"
            }
          />
          <ViewTab
            active={view === "people"}
            onClick={() => setView("people")}
            icon={<Users className="h-4 w-4" />}
            label="People"
            description="Project access and invitations"
          />
        </nav>
        {project && (
          <p className="mt-2 text-xs text-muted-foreground">
            Owned by {project.workspace_name}
            {!project.can_edit && " · View only"}
          </p>
        )}
      </header>
      {activeProjectId == null ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted-foreground">
          Open workspace navigation and select a project to get started.
        </div>
      ) : projectLoading ? (
        <p role="status" className="p-6 text-sm text-muted-foreground">
          Checking project access…
        </p>
      ) : projectError || !project ? (
        <div role="alert" className="p-6">
          <h2 className="text-lg font-semibold">Project unavailable</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            This project may have been removed, or your access has changed.
            Choose another project from navigation or ask its owner for access.
          </p>
        </div>
      ) : view === "people" ? (
        <PeoplePage
          key={`${activeProjectId}:${accessRevision}`}
          project={project}
        />
      ) : view === "control" ? (
        <ControlRoom
          key={`${activeProjectId}:${accessRevision}`}
          projectId={activeProjectId}
          lastEvent={lastEvent}
        />
      ) : view === "knowledge" ? (
        <KnowledgePanel
          key={`${activeProjectId}:${accessRevision}`}
          projectId={activeProjectId}
          lastEvent={lastEvent}
        />
      ) : activeSubprojectId == null ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Select a subproject from the sidebar to open the Kanban board.
        </div>
      ) : subproject.loading && !subproject.data ? (
        <div
          className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground"
          role="status"
        >
          <Loader2
            className="h-5 w-5 animate-spin text-muted-foreground"
            aria-hidden
          />
          Loading execution workbench…
        </div>
      ) : subproject.error ? (
        <div
          className="flex flex-1 items-center justify-center text-sm text-destructive"
          role="alert"
        >
          {subproject.error.message}
        </div>
      ) : subproject.data ? (
        <ResizableSplit
          direction="vertical"
          defaultSize={160}
          minSize={72}
          maxSize={480}
          storageKey="taskable.kanban.headerHeight"
          separatorLabel="Resize Kanban context"
          first={
            <SubprojectHeader
              subproject={subproject.data}
              onSaved={() => subproject.refetch()}
              onDeleted={() => subproject.refetch()}
            />
          }
          second={
            <KanbanBoard
              subproject={subproject.data}
              onTicketClick={(id) => openTicket(id)}
              onSubprojectRefetch={() => subproject.refetch()}
              lastEvent={lastEvent}
            />
          }
        />
      ) : null}
    </main>
  );
}

interface ViewTabProps {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  description: string;
}

function ViewTab({ active, onClick, icon, label, description }: ViewTabProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex min-w-0 flex-col items-center justify-center gap-1 overflow-hidden border px-1 py-2 text-center transition-colors sm:flex-row sm:justify-start sm:gap-2 sm:px-3 sm:text-left",
        active
          ? "border-brand-brass bg-brand-brass/10 text-foreground"
          : "border-border bg-background/40 text-muted-foreground hover:bg-accent/40",
      )}
    >
      <span className={cn("shrink-0", active && "text-brand-brass")}>
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-xs font-semibold sm:truncate">{label}</span>
        <span className="hidden truncate text-[10px] text-muted-foreground sm:block">
          {description}
        </span>
      </span>
    </button>
  );
}
