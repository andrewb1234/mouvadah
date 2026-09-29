import { useEffect, useState } from "react";
import {
  FolderPlus,
  Plus,
  Folder,
  FileText,
  Loader2,
  Trash2,
  LogOut,
  Settings,
  Github,
} from "lucide-react";
import { MouvadahLockup } from "@/components/brand/mouvadah-brand";
import {
  createProject,
  createSubproject,
  deleteProject,
  deleteSubproject,
  listProjects,
  listSubprojects,
} from "@/lib/api";
import { useWorkspace } from "@/context/WorkspaceContext";
import { useAuth } from "@/context/AuthContext";
import { useAsync } from "@/hooks/useAsync";
import type { Project, SSEPayload, Subproject } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

interface SidebarProps {
  lastEvent: SSEPayload | null;
  onNavigateProfile: () => void;
  onNavigate?: () => void;
}

export function Sidebar({
  lastEvent,
  onNavigateProfile,
  onNavigate,
}: SidebarProps) {
  const {
    activeProjectId,
    activeSubprojectId,
    setActiveProjectId,
    setActiveSubprojectId,
  } = useWorkspace();
  const { user, logout } = useAuth();

  const projects = useAsync<Project[]>(() => listProjects(), [], {
    cacheKey: "projects",
  });

  useEffect(() => {
    if (!lastEvent) return;
    if (
      lastEvent.action === "SYNC_REQUIRED" ||
      lastEvent.action === "PROJECT_ACCESS_CHANGED" ||
      lastEvent.action === "PROJECT_CREATED" ||
      lastEvent.action === "PROJECT_DELETED"
    ) {
      projects.refetch();
    }
    // If the active project was deleted elsewhere, clear selection.
    if (
      lastEvent.action === "PROJECT_DELETED" &&
      lastEvent.entity_id === activeProjectId
    ) {
      setActiveProjectId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent]);

  async function handleDeleteProject(project: Project) {
    if (
      !window.confirm(
        `Delete "${project.name}" and every subproject, ticket, and knowledge node underneath?`,
      )
    ) {
      return;
    }
    if (activeProjectId === project.id) setActiveProjectId(null);
    try {
      await deleteProject(project.id);
    } catch {
      projects.refetch();
    }
  }

  // Default-select the first project once data arrives.
  useEffect(() => {
    if (activeProjectId == null && projects.data && projects.data.length > 0) {
      setActiveProjectId(projects.data[0].id, projects.data[0].name);
    }
  }, [activeProjectId, projects.data, setActiveProjectId]);

  return (
    <aside className="flex h-full w-full min-w-0 flex-col border-r border-border bg-card">
      <header className="border-b border-border px-4 pb-4 pt-5 max-md:pr-14">
        <div className="flex min-w-0 items-center">
          <h1>
            <MouvadahLockup size="sm" />
          </h1>
        </div>
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Projects, execution, and durable context in one accountable workspace.
        </p>
      </header>

      <ScrollArea className="flex-1">
        <div className="space-y-4 px-3 py-5">
          <section>
            <div className="mb-3 flex items-center justify-between px-1">
              <span className="technical-label">Your projects</span>
              <NewProjectButton onCreated={projects.refetch} />
            </div>
            {projects.loading && (
              <div className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Loading…
              </div>
            )}
            {projects.error && (
              <p className="px-2 py-2 text-xs text-destructive">
                {projects.error.message}
              </p>
            )}
            {projects.data?.length === 0 && !projects.loading && (
              <p className="px-2 py-2 text-xs text-muted-foreground">
                No projects yet. Create one to begin.
              </p>
            )}
            {(["workspace", "project"] as const).map((source) => (
              <div key={source}>
                {source === "project" && (
                  <h2 className="technical-label mb-3 mt-6 px-1">
                    Shared with me
                  </h2>
                )}
                <ul className="space-y-1">
                  {projects.data
                    ?.filter((project) => project.access_source === source)
                    .map((project) => (
                      <li key={project.id}>
                        <div
                          className={cn(
                            "group flex items-center gap-1 border-l-2 pr-1 transition-colors",
                            activeProjectId === project.id
                              ? "border-brand-brass bg-accent text-accent-foreground"
                              : "border-transparent hover:bg-accent/50",
                          )}
                        >
                          <button
                            className="flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left text-sm md:min-h-0"
                            onClick={() => {
                              setActiveProjectId(project.id, project.name);
                              onNavigate?.();
                            }}
                            title={project.name}
                          >
                            <Folder className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate">{project.name}</span>
                          </button>
                          {project.can_delete_project && (
                            <button
                              type="button"
                              className="h-11 w-11 shrink-0 rounded text-muted-foreground opacity-60 transition-opacity hover:bg-destructive/20 hover:text-destructive md:h-7 md:w-7 md:opacity-0 md:group-hover:opacity-100"
                              aria-label={`Delete ${project.name}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                handleDeleteProject(project);
                              }}
                            >
                              <Trash2 className="mx-auto h-3 w-3" />
                            </button>
                          )}
                        </div>
                        {activeProjectId === project.id && (
                          <SubprojectList
                            canEdit={project.can_edit}
                            projectId={project.id}
                            lastEvent={lastEvent}
                            activeSubprojectId={activeSubprojectId}
                            onSelect={setActiveSubprojectId}
                            onNavigate={onNavigate}
                          />
                        )}
                      </li>
                    ))}
                </ul>
                {source === "project" &&
                  !projects.data?.some(
                    (p) => p.access_source === "project",
                  ) && (
                    <p className="px-1 text-xs text-muted-foreground">
                      Projects shared with you appear here.
                    </p>
                  )}
              </div>
            ))}
          </section>
        </div>
      </ScrollArea>

      <footer className="border-t border-border bg-surface-subtle px-3 py-3">
        <a
          href="https://github.com/andrewb1234/mouvadah"
          target="_blank"
          rel="noreferrer"
          className="focus-ring mb-2 inline-flex min-h-9 items-center gap-2 rounded-sm px-2 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <Github className="h-3.5 w-3.5" aria-hidden="true" />
          Source code
        </a>
        <div className="flex items-center gap-2">
          {user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={user.name}
              className="h-7 w-7 rounded-full"
            />
          ) : (
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
              {user?.name?.charAt(0).toUpperCase() ?? "?"}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">{user?.name}</p>
            <p className="truncate text-[10px] text-muted-foreground">
              {user?.email}
            </p>
          </div>
          <button
            type="button"
            className="h-11 w-11 shrink-0 rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground md:h-7 md:w-7"
            aria-label="Profile & settings"
            onClick={() => {
              onNavigate?.();
              onNavigateProfile();
            }}
          >
            <Settings className="mx-auto h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            className="h-11 w-11 shrink-0 rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground md:h-7 md:w-7"
            aria-label="Sign out"
            onClick={logout}
          >
            <LogOut className="mx-auto h-3.5 w-3.5" />
          </button>
        </div>
      </footer>
    </aside>
  );
}

function SubprojectList({
  canEdit,
  projectId,
  lastEvent,
  activeSubprojectId,
  onSelect,
  onNavigate,
}: {
  canEdit: boolean;
  projectId: number;
  lastEvent: SSEPayload | null;
  activeSubprojectId: number | null;
  onSelect: (id: number | null, name?: string | null) => void;
  onNavigate?: () => void;
}) {
  const subprojects = useAsync<Subproject[]>(
    () => listSubprojects(projectId),
    [projectId],
    { cacheKey: `projects:${projectId}:subprojects` },
  );

  useEffect(() => {
    if (!lastEvent) return;
    if (
      lastEvent.action === "SYNC_REQUIRED" ||
      lastEvent.action === "SUBPROJECT_CREATED" ||
      lastEvent.action === "SUBPROJECT_UPDATED" ||
      lastEvent.action === "SUBPROJECT_DELETED"
    ) {
      subprojects.refetch();
    }
    if (
      lastEvent.action === "SUBPROJECT_DELETED" &&
      lastEvent.entity_id === activeSubprojectId
    ) {
      onSelect(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent]);

  useEffect(() => {
    if (
      activeSubprojectId != null &&
      subprojects.data &&
      !subprojects.data.some(
        (subproject) => subproject.id === activeSubprojectId,
      )
    ) {
      onSelect(null);
    }
  }, [activeSubprojectId, subprojects.data, onSelect]);

  async function handleDelete(sub: Subproject) {
    if (
      !window.confirm(`Delete subproject "${sub.name}" and all of its tickets?`)
    ) {
      return;
    }
    if (activeSubprojectId === sub.id) onSelect(null);
    try {
      await deleteSubproject(sub.id);
    } catch {
      subprojects.refetch();
    }
  }

  return (
    <div className="ml-4 mt-1 border-l border-border pl-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Subprojects
        </span>
        {canEdit && (
          <NewSubprojectButton
            projectId={projectId}
            onCreated={subprojects.refetch}
          />
        )}
      </div>
      <ul className="space-y-0.5">
        {subprojects.data?.map((sub) => (
          <li key={sub.id}>
            <div
              className={cn(
                "group flex items-center gap-1 pr-1 transition-colors",
                activeSubprojectId === sub.id
                  ? "bg-primary/10 text-foreground"
                  : "text-muted-foreground hover:bg-accent/40",
              )}
            >
              <button
                className="flex min-h-11 min-w-0 flex-1 items-center gap-2 px-2 py-1 text-left text-xs md:min-h-0"
                onClick={() => {
                  onSelect(sub.id, sub.name);
                  onNavigate?.();
                }}
                title={sub.name}
              >
                <FileText className="h-3 w-3 shrink-0" />
                <span className="truncate">{sub.name}</span>
                <span className="ml-auto rounded bg-muted px-1 text-[9px] uppercase text-muted-foreground">
                  {sub.status.slice(0, 4)}
                </span>
              </button>
              {canEdit && (
                <button
                  type="button"
                  className="h-11 w-11 shrink-0 rounded text-muted-foreground opacity-60 transition-opacity hover:bg-destructive/20 hover:text-destructive md:h-6 md:w-6 md:opacity-0 md:group-hover:opacity-100"
                  aria-label={`Delete ${sub.name}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(sub);
                  }}
                >
                  <Trash2 className="mx-auto h-3 w-3" />
                </button>
              )}
            </div>
          </li>
        ))}
        {subprojects.data?.length === 0 && (
          <li className="px-2 py-1 text-[11px] text-muted-foreground">
            None yet.
          </li>
        )}
      </ul>
    </div>
  );
}

function NewProjectButton({ onCreated }: { onCreated: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createProject({
        name: name.trim(),
        description: desc.trim() || undefined,
      });
      setName("");
      setDesc("");
      setExpanded(false);
      onCreated();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Project creation failed.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!expanded) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6"
        onClick={() => setExpanded(true)}
        aria-label="New project"
      >
        <FolderPlus className="h-3.5 w-3.5" />
      </Button>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="absolute inset-x-3 top-12 z-20 space-y-2 rounded-md border border-border bg-popover p-3 text-xs shadow-lg"
    >
      <Input
        autoFocus
        aria-label="Project name"
        placeholder="Project name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="h-8 text-xs"
      />
      <Textarea
        aria-label="Project description"
        placeholder="Description (optional)"
        value={desc}
        onChange={(e) => setDesc(e.target.value)}
        className="min-h-[60px] text-xs"
      />
      {error && (
        <p className="text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded(false)}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "…" : "Create"}
        </Button>
      </div>
    </form>
  );
}

function NewSubprojectButton({
  projectId,
  onCreated,
}: {
  projectId: number;
  onCreated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createSubproject(projectId, {
        name: name.trim(),
        context_brief: brief.trim(),
      });
      setName("");
      setBrief("");
      setExpanded(false);
      onCreated();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Subproject creation failed.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!expanded) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="h-5 w-5"
        onClick={() => setExpanded(true)}
        aria-label="New subproject"
      >
        <Plus className="h-3 w-3" />
      </Button>
    );
  }

  return (
    <form
      onSubmit={submit}
      className="ml-2 mt-1 space-y-2 rounded-md border border-border bg-popover p-2 text-xs"
    >
      <Input
        autoFocus
        aria-label="Subproject name"
        placeholder="Subproject"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="h-7 text-xs"
      />
      <Textarea
        aria-label="Subproject context brief"
        placeholder="Context brief for the agent"
        value={brief}
        onChange={(e) => setBrief(e.target.value)}
        className="min-h-[60px] text-xs"
      />
      {error && (
        <p className="text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-1">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded(false)}
        >
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "…" : "Add"}
        </Button>
      </div>
    </form>
  );
}
