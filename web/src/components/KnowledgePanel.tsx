import { useWorkspace } from "@/context/WorkspaceContext";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  ExternalLink,
  Flag,
  Loader2,
  Map as MapIcon,
  MessageSquare,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ResizableSplit } from "@/components/ui/resizable-split";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Surface } from "@/components/ui/surface";
import { TechnicalLabel } from "@/components/ui/technical-label";
import { useAsync } from "@/hooks/useAsync";
import {
  createKnowledgeNode,
  deleteKnowledgeNode,
  getContextTrail,
  listKnowledgeNodesAll,
  listProposalsForNode,
  reviewProposal,
  updateKnowledgeNode,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  ContextTrail,
  KnowledgeNode,
  KnowledgeNodeStatus,
  KnowledgeNodeType,
  KnowledgeProposal,
  SSEPayload,
} from "@/types";
import { KNOWLEDGE_NODE_TYPE_LABELS, KNOWLEDGE_NODE_TYPES } from "@/types";

interface Props {
  projectId: number;
  lastEvent: SSEPayload | null;
}

/**
 * Renders the per-project knowledge tree: a collapsible left column of
 * nodes plus a right-hand editor for the selection. Listens to SSE events
 * so agent-side mutations reconcile live. Styled with a type-coded left
 * border (navy / mustard / emerald / sky) to keep the four node types
 * scannable without fighting the existing palette.
 */
export function KnowledgePanel({ projectId, lastEvent }: Props) {
  const { project } = useWorkspace();
  const nodes = useAsync<KnowledgeNode[]>(
    () => listKnowledgeNodesAll(projectId),
    [projectId],
  );
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [creatingUnder, setCreatingUnder] = useState<number | null | "root">(
    null,
  );
  const [trailQuery, setTrailQuery] = useState("");
  const [trail, setTrail] = useState<ContextTrail | null>(null);
  const [trailLoading, setTrailLoading] = useState(false);
  const [trailError, setTrailError] = useState<string | null>(null);
  const [checkpointSaving, setCheckpointSaving] = useState(false);
  const [checkpointMessage, setCheckpointMessage] = useState<string | null>(
    null,
  );
  const isNarrow = useNarrowKnowledgeLayout();
  const [showMobileEditor, setShowMobileEditor] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const lastSelectedNodeRef = useRef<KnowledgeNode | null>(null);
  const mapRef = useRef<HTMLElement | null>(null);

  // SSE-driven refetch: any knowledge mutation for this project refreshes
  // the whole panel. The endpoint is cheap (single SELECT) so we don't
  // bother with targeted updates.
  useEffect(() => {
    if (!lastEvent) return;
    if (
      lastEvent.action === "SYNC_REQUIRED" ||
      (lastEvent.entity === "knowledge_node" &&
        lastEvent.parent_id === projectId)
    ) {
      nodes.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent, projectId]);

  // Expand roots once. Deeper branches remain scannable and user-controlled.
  useEffect(() => {
    if (nodes.data && expanded.size === 0 && nodes.data.length > 0) {
      setExpanded(
        new Set(
          nodes.data
            .filter((node) => node.parent_id === null)
            .map((node) => node.id),
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes.data]);

  const selectedNode = useMemo(
    () => nodes.data?.find((n) => n.id === selectedId) ?? null,
    [nodes.data, selectedId],
  );
  if (selectedNode) lastSelectedNodeRef.current = selectedNode;
  const selectedNodeWasDeleted =
    selectedId !== null &&
    Boolean(nodes.data) &&
    !selectedNode &&
    lastSelectedNodeRef.current?.id === selectedId;
  const editorNode =
    selectedNode ??
    (selectedNodeWasDeleted ? lastSelectedNodeRef.current : null);

  const childrenByParent = useMemo(() => {
    const map = new Map<number | null, KnowledgeNode[]>();
    for (const node of nodes.data ?? []) {
      const bucket = map.get(node.parent_id) ?? [];
      bucket.push(node);
      map.set(node.parent_id, bucket);
    }
    for (const bucket of map.values()) {
      bucket.sort((a, b) => a.created_at.localeCompare(b.created_at));
    }
    return map;
  }, [nodes.data]);

  function toggleExpanded(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const selectNode = useCallback(
    (id: number) => {
      if (
        editorDirty &&
        id !== selectedId &&
        !window.confirm("Discard unsaved changes and open another node?")
      ) {
        return;
      }
      setSelectedId(id);
      if (isNarrow) setShowMobileEditor(true);
    },
    [editorDirty, isNarrow, selectedId],
  );

  async function runContextTrail(nextQuery = trailQuery) {
    setTrailLoading(true);
    setTrailError(null);
    setCheckpointMessage(null);
    try {
      const result = await getContextTrail(projectId, nextQuery, 6);
      setTrail(result);
      setExpanded((prev) => {
        const next = new Set(prev);
        for (const segment of result.load_order) next.add(segment.id);
        return next;
      });
      if (result.items[0]) selectNode(result.items[0].id);
    } catch (error) {
      setTrailError(
        error instanceof Error ? error.message : "Context trail failed.",
      );
    } finally {
      setTrailLoading(false);
    }
  }

  async function saveContextCheckpoint() {
    if (!trail || trail.load_order.length === 0) return;
    setCheckpointSaving(true);
    setTrailError(null);
    setCheckpointMessage(null);
    const title =
      trail.query.trim().length > 0
        ? `Context checkpoint: ${trail.query.trim()}`
        : "Context checkpoint";
    const sourceRefs = trail.load_order.map((segment) => `node:${segment.id}`);
    const lines = [
      "# Context checkpoint",
      "",
      `Query: ${trail.query.trim() || "(empty)"}`,
      "",
      "## Loaded nodes",
      ...trail.load_order.map(
        (segment, index) =>
          `${index + 1}. [${segment.node_type}] #${segment.id} ${segment.title}`,
      ),
      "",
      "## Agent belief to verify",
      "Fill this in after the agent uses the trail, then keep or correct the branch.",
    ];
    try {
      const node = await createKnowledgeNode(projectId, {
        title,
        node_type: "SUMMARY",
        content: lines.join("\n"),
        parent_id: trail.items[0]?.id ?? null,
        source_refs: sourceRefs,
      });
      selectNode(node.id);
      setCheckpointMessage(
        `Checkpoint #${node.id} created with ${sourceRefs.length} exact source ${
          sourceRefs.length === 1 ? "reference" : "references"
        }.`,
      );
      nodes.refetch();
    } catch (error) {
      setTrailError(
        error instanceof Error ? error.message : "Checkpoint creation failed.",
      );
    } finally {
      setCheckpointSaving(false);
    }
  }

  const knowledgeCounts = useMemo(
    () => ({
      current:
        nodes.data?.filter((node) => node.status === "CURRENT").length ?? 0,
      stale: nodes.data?.filter((node) => node.status === "STALE").length ?? 0,
      archived:
        nodes.data?.filter((node) => node.status === "ARCHIVED").length ?? 0,
    }),
    [nodes.data],
  );

  const tree = (
    <aside
      ref={mapRef}
      tabIndex={-1}
      className="flex h-full w-full flex-col border-r border-border bg-card/20"
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">
            Knowledge map
          </h3>
          <p className="text-[11px] text-muted-foreground">
            Evidence → summary → decision
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-7 px-2 text-xs"
          disabled={!project?.can_edit}
          onClick={() => setCreatingUnder("root")}
        >
          <Plus className="mr-1 h-3 w-3" />
          Root node
        </Button>
      </header>
      <ScrollArea className="flex-1">
        <div className="px-2 py-2">
          {nodes.loading && !nodes.data && (
            <div className="flex items-center gap-2 px-2 py-4 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Loading…
            </div>
          )}
          {nodes.error && (
            <div className="space-y-2 px-2 py-2" role="alert">
              <p className="text-xs text-destructive">{nodes.error.message}</p>
              <Button size="sm" variant="outline" onClick={nodes.refetch}>
                Retry knowledge map
              </Button>
            </div>
          )}
          {nodes.data && nodes.data.length === 0 && (
            <p className="px-2 py-4 text-xs text-muted-foreground">
              No knowledge nodes yet. Use the agent's{" "}
              <span className="font-mono">create_knowledge_node</span> tool or
              click <em>Root node</em> to start.
            </p>
          )}
          {creatingUnder === "root" && (
            <div className="mb-2">
              <NewNodeForm
                projectId={projectId}
                parentId={null}
                onCancel={() => setCreatingUnder(null)}
                onCreated={(node) => {
                  setCreatingUnder(null);
                  selectNode(node.id);
                  nodes.refetch();
                }}
              />
            </div>
          )}
          <TreeBranch
            parentId={null}
            depth={0}
            childrenByParent={childrenByParent}
            selectedId={selectedId}
            expanded={expanded}
            creatingUnder={creatingUnder}
            onSelect={selectNode}
            onToggle={toggleExpanded}
            onStartCreate={(id) => {
              setCreatingUnder(id);
              setExpanded((prev) => new Set(prev).add(id));
            }}
            onCancelCreate={() => setCreatingUnder(null)}
            onCreated={(node) => {
              setCreatingUnder(null);
              selectNode(node.id);
              nodes.refetch();
            }}
            projectId={projectId}
          />
        </div>
      </ScrollArea>
    </aside>
  );

  const editor = (
    <section
      className="flex h-full w-full flex-1 flex-col overflow-hidden"
      aria-label="Knowledge node review"
    >
      {editorNode ? (
        <NodeEditor
          key={editorNode.id}
          node={editorNode}
          remoteDeleted={selectedNodeWasDeleted}
          allNodes={nodes.data ?? []}
          lastEvent={lastEvent}
          onSaved={() => nodes.refetch()}
          onSelectNode={selectNode}
          onDirtyChange={setEditorDirty}
          onDeleted={() => {
            setSelectedId(null);
            setEditorDirty(false);
            lastSelectedNodeRef.current = null;
            nodes.refetch();
          }}
        />
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          Select a node on the left to review or edit.
        </div>
      )}
    </section>
  );

  return (
    <section
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      aria-labelledby="knowledge-workbench-title"
    >
      <header className="shrink-0 border-b border-border bg-card/30 px-4 py-4 sm:px-6">
        <TechnicalLabel>Provenance and review</TechnicalLabel>
        <div className="mt-2 flex flex-col justify-between gap-3 lg:flex-row lg:items-end">
          <div>
            <h2
              id="knowledge-workbench-title"
              className="text-xl font-semibold tracking-tight sm:text-2xl"
            >
              Knowledge workbench
            </h2>
            <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              Trace durable decisions back to evidence, review proposed changes,
              and load only the context an agent needs.
            </p>
          </div>
          <div
            className="flex flex-wrap gap-2"
            aria-label="Knowledge status summary"
          >
            <KnowledgeStatusBadge
              status="CURRENT"
              count={knowledgeCounts.current}
            />
            <KnowledgeStatusBadge
              status="STALE"
              count={knowledgeCounts.stale}
            />
            <KnowledgeStatusBadge
              status="ARCHIVED"
              count={knowledgeCounts.archived}
            />
          </div>
        </div>
      </header>
      <ContextTrailPanel
        query={trailQuery}
        trail={trail}
        loading={trailLoading}
        error={trailError}
        checkpointSaving={checkpointSaving}
        checkpointMessage={checkpointMessage}
        onQueryChange={setTrailQuery}
        onRun={runContextTrail}
        onSelectNode={selectNode}
        onSaveCheckpoint={saveContextCheckpoint}
      />
      <div className="min-h-0 flex-1">
        {isNarrow ? (
          <div className="h-full min-h-0">
            <div className={cn("h-full", showMobileEditor && "hidden")}>
              {tree}
            </div>
            <div
              className={cn(
                "h-full min-h-0 flex-col",
                showMobileEditor ? "flex" : "hidden",
              )}
            >
              <div className="shrink-0 border-b border-border bg-surface-subtle px-3 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowMobileEditor(false);
                    requestAnimationFrame(() => mapRef.current?.focus());
                  }}
                >
                  <ArrowLeft className="h-4 w-4" aria-hidden />
                  Back to knowledge map
                </Button>
              </div>
              <div className="min-h-0 flex-1">{editor}</div>
            </div>
          </div>
        ) : (
          <ResizableSplit
            direction="horizontal"
            defaultSize={320}
            minSize={240}
            maxSize={640}
            storageKey="taskable.knowledge.treeWidth"
            separatorLabel="Resize knowledge map"
            first={tree}
            second={editor}
          />
        )}
      </div>
    </section>
  );
}

interface ContextTrailPanelProps {
  query: string;
  trail: ContextTrail | null;
  loading: boolean;
  error: string | null;
  checkpointSaving: boolean;
  checkpointMessage: string | null;
  onQueryChange: (query: string) => void;
  onRun: (query?: string) => void;
  onSelectNode: (id: number) => void;
  onSaveCheckpoint: () => void;
}

function ContextTrailPanel({
  query,
  trail,
  loading,
  error,
  checkpointSaving,
  checkpointMessage,
  onQueryChange,
  onRun,
  onSelectNode,
  onSaveCheckpoint,
}: ContextTrailPanelProps) {
  const { project } = useWorkspace();
  const hasTrail = trail !== null;
  return (
    <section className="border-b border-border bg-card/20 px-4 py-3">
      <div className="mb-2">
        <h3 className="text-sm font-semibold">Intent-scoped context trail</h3>
        <p className="text-xs text-muted-foreground">
          Describe the work. Mouvadah returns a recommended load order and why
          each branch matched.
        </p>
      </div>
      <form
        className="flex flex-col gap-2 sm:flex-row sm:items-end"
        onSubmit={(event) => {
          event.preventDefault();
          onRun();
        }}
      >
        <div className="min-w-0 flex-1">
          <label
            htmlFor="knowledge-context-intent"
            className="mb-1 block text-xs font-medium"
          >
            What context are you trying to load?
          </label>
          <div className="flex items-center gap-2">
            <MapIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
            <Input
              id="knowledge-context-intent"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder="Find context trail, e.g. battle component"
              className="h-8 text-xs"
            />
          </div>
        </div>
        <div className="flex gap-2">
          <Button type="submit" size="sm" disabled={loading}>
            <Search className="mr-1 h-3.5 w-3.5" />
            {loading ? "Finding…" : "Find trail"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={
              !project?.can_edit ||
              checkpointSaving ||
              !trail ||
              trail.load_order.length === 0
            }
            onClick={onSaveCheckpoint}
          >
            {checkpointSaving ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Flag className="mr-1 h-3.5 w-3.5" />
            )}
            Save checkpoint
          </Button>
        </div>
      </form>

      {error && (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      {checkpointMessage && (
        <p className="mt-2 text-xs text-status-done-foreground" role="status">
          {checkpointMessage}
        </p>
      )}
      {hasTrail && (
        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
          <div className="min-w-0">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Load order
            </div>
            {trail.load_order.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No matching nodes. Add clearer signpost text to the tree or try
                another query.
              </p>
            ) : (
              <div className="flex flex-wrap gap-1">
                {trail.load_order.map((segment, index) => (
                  <button
                    key={segment.id}
                    type="button"
                    onClick={() => onSelectNode(segment.id)}
                    className="inline-flex max-w-[240px] items-center gap-1 rounded-md border border-border bg-background/60 px-2 py-1 text-[11px] hover:border-primary/50 hover:bg-accent/60"
                    title={segment.title}
                  >
                    <span className="text-muted-foreground">{index + 1}</span>
                    <TypeBadge type={segment.node_type} />
                    <span className="truncate">{segment.title}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="min-w-0">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Matched branches
            </div>
            <div className="flex max-h-48 flex-col gap-1 overflow-auto pr-1">
              {trail.items.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No branches matched this intent. Try a concrete deliverable,
                  decision, or system name.
                </p>
              )}
              {trail.items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelectNode(item.id)}
                  className="rounded-md border border-border/60 bg-background/50 px-2 py-1.5 text-left text-xs hover:border-primary/50 hover:bg-accent/50"
                >
                  <div className="flex min-w-0 items-center gap-1.5">
                    <TypeBadge type={item.node_type} />
                    <span className="truncate font-medium">{item.title}</span>
                  </div>
                  <p className="mt-1 text-[11px] leading-snug text-foreground/80">
                    {item.reason}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-muted-foreground">
                    <span className="truncate">
                      {item.path.map((part) => part.title).join(" > ")}
                    </span>
                    {item.matched_terms.length > 0 && (
                      <span>Matched: {item.matched_terms.join(", ")}</span>
                    )}
                    <span>Score {item.score}</span>
                  </div>
                  {item.children.map((child) => (
                    <div
                      key={child.id}
                      className="mt-1 rounded-sm bg-surface-subtle px-2 py-1 text-[10px] text-muted-foreground"
                    >
                      Child hint: {child.title}
                      {child.content_preview
                        ? ` — ${child.content_preview}`
                        : ""}
                    </div>
                  ))}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

interface TreeBranchProps {
  parentId: number | null;
  depth: number;
  childrenByParent: Map<number | null, KnowledgeNode[]>;
  selectedId: number | null;
  expanded: Set<number>;
  creatingUnder: number | null | "root";
  projectId: number;
  onSelect: (id: number) => void;
  onToggle: (id: number) => void;
  onStartCreate: (parentId: number) => void;
  onCancelCreate: () => void;
  onCreated: (node: KnowledgeNode) => void;
}

function TreeBranch(props: TreeBranchProps) {
  const { project } = useWorkspace();
  const siblings = props.childrenByParent.get(props.parentId) ?? [];
  return (
    <ul
      className="space-y-0.5"
      aria-label={props.depth === 0 ? "Knowledge hierarchy" : undefined}
    >
      {siblings.map((node) => {
        const children = props.childrenByParent.get(node.id) ?? [];
        const hasChildren = children.length > 0;
        const isExpanded = props.expanded.has(node.id);
        const isSelected = props.selectedId === node.id;
        return (
          <li key={node.id}>
            <div
              className={cn(
                "group flex min-h-9 items-center gap-1 rounded-md border-l-2 py-1 pl-1.5 pr-1 text-xs transition-colors",
                TYPE_BORDER[node.node_type],
                isSelected
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/40",
              )}
              style={{ marginLeft: `${Math.min(props.depth, 6) * 10}px` }}
            >
              <button
                type="button"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded hover:bg-accent"
                onClick={() => hasChildren && props.onToggle(node.id)}
                aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.title}`}
                disabled={!hasChildren}
              >
                {hasChildren ? (
                  isExpanded ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )
                ) : (
                  <span className="h-1 w-1 rounded-full bg-muted-foreground/40" />
                )}
              </button>
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => props.onSelect(node.id)}
                aria-current={isSelected ? "true" : undefined}
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <TypeBadge type={node.node_type} />
                  <KnowledgeStatusBadge status={node.status} compact />
                  <span className="truncate font-medium" title={node.title}>
                    {node.title}
                  </span>
                </span>
                <span className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  {node.created_by === "AGENT" ? (
                    <Bot className="h-3 w-3" aria-hidden />
                  ) : (
                    <UserRound className="h-3 w-3" aria-hidden />
                  )}
                  <span>{node.created_by === "AGENT" ? "Agent" : "Human"}</span>
                  <span aria-hidden>·</span>
                  <span>
                    {children.length}{" "}
                    {children.length === 1 ? "child" : "children"}
                  </span>
                </span>
              </button>
              <button
                type="button"
                className="h-7 w-7 shrink-0 rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                aria-label={`Add child under ${node.title}`}
                disabled={!project?.can_edit}
                onClick={() => props.onStartCreate(node.id)}
              >
                <Plus className="mx-auto h-3 w-3" />
              </button>
            </div>
            {isExpanded && (
              <>
                {props.creatingUnder === node.id && (
                  <div
                    style={{ marginLeft: `${(props.depth + 1) * 10 + 6}px` }}
                  >
                    <NewNodeForm
                      projectId={props.projectId}
                      parentId={node.id}
                      onCancel={props.onCancelCreate}
                      onCreated={props.onCreated}
                    />
                  </div>
                )}
                {hasChildren && (
                  <TreeBranch
                    {...props}
                    parentId={node.id}
                    depth={props.depth + 1}
                  />
                )}
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

interface NewNodeFormProps {
  projectId: number;
  parentId: number | null;
  onCancel: () => void;
  onCreated: (node: KnowledgeNode) => void;
}

function NewNodeForm({
  projectId,
  parentId,
  onCancel,
  onCreated,
}: NewNodeFormProps) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<KnowledgeNodeType>("RAW");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const node = await createKnowledgeNode(projectId, {
        title: title.trim(),
        node_type: type,
        content: "",
        parent_id: parentId,
      });
      onCreated(node);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Node creation failed.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      aria-label={
        parentId === null
          ? "Create root knowledge node"
          : `Create child knowledge node under #${parentId}`
      }
      className="my-1 space-y-2 rounded-md border border-border bg-popover p-2 text-xs"
    >
      <label className="block space-y-1">
        <span className="font-medium">Node title</span>
        <Input
          autoFocus
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Decision, evidence, or requirement"
          className="h-8 text-xs"
        />
      </label>
      <label className="block space-y-1">
        <span className="font-medium">Node type</span>
        <Select
          value={type}
          onValueChange={(v) => setType(v as KnowledgeNodeType)}
        >
          <SelectTrigger className="h-8 text-xs" aria-label="Node type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {KNOWLEDGE_NODE_TYPES.map((t) => (
              <SelectItem key={t} value={t}>
                {KNOWLEDGE_NODE_TYPE_LABELS[t]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>
      {error && (
        <p className="text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-1">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving}>
          {saving ? "…" : "Create"}
        </Button>
      </div>
    </form>
  );
}

interface NodeEditorProps {
  node: KnowledgeNode;
  remoteDeleted: boolean;
  allNodes: KnowledgeNode[];
  lastEvent: SSEPayload | null;
  onSaved: () => void;
  onDeleted: () => void;
  onSelectNode: (id: number) => void;
  onDirtyChange: (dirty: boolean) => void;
}

function NodeEditor({
  node,
  remoteDeleted,
  allNodes,
  lastEvent,
  onSaved,
  onDeleted,
  onSelectNode,
  onDirtyChange,
}: NodeEditorProps) {
  const { project } = useWorkspace();
  const [title, setTitle] = useState(node.title);
  const [type, setType] = useState<KnowledgeNodeType>(node.node_type);
  const [content, setContent] = useState(node.content);
  const [sourceRefsText, setSourceRefsText] = useState(
    node.source_refs.join("\n"),
  );
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [correctionText, setCorrectionText] = useState("");
  const [correctionSaving, setCorrectionSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remoteUpdate, setRemoteUpdate] = useState<KnowledgeNode | null>(null);
  const [baseline, setBaseline] = useState(() => knowledgeDraft(node));

  // Build an id → KnowledgeNode lookup so ``node:N`` source refs can render
  // a live, clickable chip showing the current title of the referenced
  // node. Re-derives on every render so SSE-driven title edits elsewhere
  // propagate immediately.
  const nodeById = useMemo(() => {
    const map = new Map<number, KnowledgeNode>();
    for (const n of allNodes) map.set(n.id, n);
    return map;
  }, [allNodes]);

  const resolvedRefs = useMemo(
    () =>
      baseline.sourceRefsText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const match = line.match(/^node:(\d+)$/i);
          if (!match) {
            return {
              kind: /^https?:\/\//i.test(line)
                ? ("url" as const)
                : ("text" as const),
              value: line,
            };
          }
          const id = Number(match[1]);
          const target = nodeById.get(id);
          return {
            kind: "node" as const,
            value: line,
            id,
            target: target ?? null,
          };
        }),
    [baseline.sourceRefsText, nodeById],
  );

  const dirty =
    title !== baseline.title ||
    type !== baseline.type ||
    content !== baseline.content ||
    sourceRefsText !== baseline.sourceRefsText;

  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (node.updated_at === baseline.updatedAt) return;
    if (dirty) {
      setRemoteUpdate(node);
      return;
    }
    applyKnowledgeDraft(node, {
      setTitle,
      setType,
      setContent,
      setSourceRefsText,
      setBaseline,
    });
    setRemoteUpdate(null);
  }, [baseline.updatedAt, dirty, node]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateKnowledgeNode(node.id, {
        title: title.trim() || baseline.title,
        node_type: type,
        content,
        source_refs: sourceRefsText
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      applyKnowledgeDraft(updated, {
        setTitle,
        setType,
        setContent,
        setSourceRefsText,
        setBaseline,
      });
      setRemoteUpdate(null);
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Node save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete "${node.title}" and all its children?`)) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteKnowledgeNode(node.id);
      onDeleted();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Node deletion failed.",
      );
    } finally {
      setDeleting(false);
    }
  }

  async function requestCorrection() {
    const correction = correctionText.trim();
    if (!correction) return;
    setCorrectionSaving(true);
    setError(null);
    try {
      const created = await createKnowledgeNode(node.project_id, {
        title: `Correction request: ${node.title}`,
        node_type: "SUMMARY",
        parent_id: node.id,
        source_refs: [`node:${node.id}`],
        content: [
          "# Human correction request",
          "",
          correction,
          "",
          "## Target node",
          `node:${node.id}`,
          "",
          "Agent: resolve this by updating the target node or adding a corrected child summary.",
        ].join("\n"),
      });
      setCorrectionText("");
      onSaved();
      onSelectNode(created.id);
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Correction request failed.",
      );
    } finally {
      setCorrectionSaving(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <header className="flex flex-col gap-3 border-b border-border bg-card/30 px-4 py-3 sm:px-6 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="grid gap-2 sm:grid-cols-[auto_minmax(0,1fr)_7rem] sm:items-center">
            <TypeBadge type={type} />
            <label className="sr-only" htmlFor={`knowledge-title-${node.id}`}>
              Node title
            </label>
            <Input
              readOnly={!project?.can_edit}
              id={`knowledge-title-${node.id}`}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="h-8 flex-1 text-sm font-semibold"
            />
            <Select
              disabled={!project?.can_edit}
              value={type}
              onValueChange={(v) => setType(v as KnowledgeNodeType)}
            >
              <SelectTrigger
                className="h-8 w-full text-xs"
                aria-label="Node type"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KNOWLEDGE_NODE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {KNOWLEDGE_NODE_TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
            <span className="uppercase tracking-wide">#{node.id}</span>
            <span>·</span>
            <Badge
              variant={node.created_by === "AGENT" ? "agent" : "human"}
              className="py-0 text-[10px]"
            >
              {node.created_by === "AGENT" ? (
                <Sparkles className="mr-1 h-2.5 w-2.5" />
              ) : null}
              {node.created_by}
            </Badge>
            <span>·</span>
            <span>updated {formatWhen(node.updated_at)}</span>
            <span>·</span>
            <Select
              disabled={!project?.can_edit}
              value={node.status ?? "CURRENT"}
              onValueChange={(v) => {
                setError(null);
                void updateKnowledgeNode(node.id, {
                  status: v as KnowledgeNodeStatus,
                })
                  .then(onSaved)
                  .catch((cause: unknown) =>
                    setError(
                      cause instanceof Error
                        ? cause.message
                        : "Status update failed.",
                    ),
                  );
              }}
            >
              <SelectTrigger
                className="h-7 w-28 px-2 text-[10px]"
                aria-label="Knowledge status"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="CURRENT">Current</SelectItem>
                <SelectItem value="STALE">Stale</SelectItem>
                <SelectItem value="ARCHIVED">Archived</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={remove}
            disabled={!project?.can_edit || deleting || remoteDeleted}
            className="text-destructive"
          >
            <Trash2 className="mr-1 h-3.5 w-3.5" />
            Delete
          </Button>
          <Button
            size="sm"
            onClick={save}
            disabled={
              !project?.can_edit ||
              !dirty ||
              saving ||
              Boolean(remoteUpdate) ||
              remoteDeleted
            }
          >
            <Save className="mr-1 h-3.5 w-3.5" />
            {saving ? "Saving…" : "Save"}
          </Button>
        </div>
      </header>
      {(remoteDeleted || remoteUpdate) && (
        <Surface
          tone="subtle"
          radius="none"
          padding="sm"
          className="border-b border-status-review-border"
          role="alert"
        >
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold">
                {remoteDeleted
                  ? "This node was deleted remotely"
                  : "Remote update available"}
              </p>
              <p className="text-xs text-muted-foreground">
                {remoteDeleted
                  ? "Your draft remains visible here so you can recover it. Saving is disabled."
                  : "Your local draft is preserved. Review the remote version before choosing what replaces it."}
              </p>
            </div>
            {remoteUpdate && (
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    applyKnowledgeDraft(remoteUpdate, {
                      setTitle,
                      setType,
                      setContent,
                      setSourceRefsText,
                      setBaseline,
                    });
                    setRemoteUpdate(null);
                  }}
                >
                  Load remote version
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    setBaseline(knowledgeDraft(remoteUpdate));
                    setRemoteUpdate(null);
                  }}
                >
                  Keep draft and overwrite
                </Button>
              </div>
            )}
          </div>
        </Surface>
      )}
      {error && (
        <p
          className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-xs text-destructive sm:px-6"
          role="alert"
        >
          {error}
        </p>
      )}
      <div className="flex flex-1 flex-col gap-4 overflow-auto px-4 py-4 sm:px-6">
        <ProposalsSection
          nodeId={node.id}
          lastEvent={lastEvent}
          onAccepted={onSaved}
        />
        <section aria-labelledby={`provenance-${node.id}`}>
          <h3
            id={`provenance-${node.id}`}
            className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
          >
            Provenance
          </h3>
          <p className="mb-2 text-xs text-muted-foreground">
            Follow exact node references or source material used for this
            knowledge claim.
          </p>
          {resolvedRefs.length === 0 && (
            <p className="mb-2 text-xs text-muted-foreground">
              No source references recorded.
            </p>
          )}
          {resolvedRefs.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1">
              {resolvedRefs.map((ref, idx) =>
                ref.kind === "node" ? (
                  <button
                    key={`${ref.value}-${idx}`}
                    type="button"
                    onClick={() => ref.target && onSelectNode(ref.target.id)}
                    disabled={!ref.target}
                    aria-label={
                      ref.target
                        ? `Open referenced node ${ref.target.title}`
                        : `Missing referenced node ${ref.id}`
                    }
                    className={cn(
                      "group inline-flex min-h-7 max-w-[260px] items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] transition-colors",
                      ref.target
                        ? "cursor-pointer border-border bg-card hover:border-primary/50 hover:bg-accent/60"
                        : "cursor-not-allowed border-destructive/30 bg-destructive/10 text-destructive",
                    )}
                  >
                    {ref.target ? (
                      <TypeBadge type={ref.target.node_type} />
                    ) : (
                      <span className="rounded bg-destructive/20 px-1 text-[9px] font-semibold uppercase">
                        Missing
                      </span>
                    )}
                    <span className="truncate">
                      #{ref.id} {ref.target?.title ?? "Missing node"}
                    </span>
                    {ref.target && (
                      <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                    )}
                  </button>
                ) : ref.kind === "url" ? (
                  <a
                    key={`${ref.value}-${idx}`}
                    href={ref.value}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex min-h-7 max-w-[260px] items-center gap-1 rounded-md border border-border bg-card px-2 py-0.5 font-mono text-[11px] hover:border-primary/50 hover:bg-accent/60"
                  >
                    <span className="truncate">{ref.value}</span>
                    <ExternalLink className="h-3 w-3 shrink-0" aria-hidden />
                  </a>
                ) : (
                  <span
                    key={`${ref.value}-${idx}`}
                    className="inline-flex min-h-7 max-w-[260px] items-center rounded-md border border-border/40 bg-muted/30 px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
                    title={ref.value}
                  >
                    <span className="truncate">{ref.value}</span>
                  </span>
                ),
              )}
            </div>
          )}
          <label
            htmlFor={`knowledge-source-refs-${node.id}`}
            className="mb-1 block text-xs font-medium"
          >
            Source references
          </label>
          <Textarea
            readOnly={!project?.can_edit}
            id={`knowledge-source-refs-${node.id}`}
            value={sourceRefsText}
            onChange={(e) => setSourceRefsText(e.target.value)}
            rows={3}
            placeholder={
              "/absolute/path/file.py\nhttps://example.com/doc\nnode:42"
            }
            className="font-mono text-xs"
          />
          <p className="mt-1 text-[10px] text-muted-foreground">
            One pointer per line. <span className="font-mono">node:N</span>{" "}
            entries resolve to current titles; missing nodes remain explicit.
          </p>
        </section>
        <section
          className="flex flex-1 flex-col"
          aria-labelledby={`content-${node.id}`}
        >
          <h3
            id={`content-${node.id}`}
            className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
          >
            Content
          </h3>
          <label className="sr-only" htmlFor={`knowledge-content-${node.id}`}>
            Node content
          </label>
          <Textarea
            readOnly={!project?.can_edit}
            id={`knowledge-content-${node.id}`}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className={cn(
              "min-h-[300px] flex-1 text-sm leading-relaxed",
              type === "RAW" && "font-mono text-xs",
            )}
            placeholder={CONTENT_PLACEHOLDERS[type]}
          />
        </section>
        <section
          className="rounded-md border border-border bg-muted/20 p-3"
          aria-labelledby={`correction-${node.id}`}
        >
          <div className="mb-2 flex items-center gap-2">
            <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
            <h3
              id={`correction-${node.id}`}
              className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              Correction request
            </h3>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="sr-only" htmlFor={`correction-text-${node.id}`}>
              Correction details
            </label>
            <Textarea
              readOnly={!project?.can_edit}
              id={`correction-text-${node.id}`}
              value={correctionText}
              onChange={(e) => setCorrectionText(e.target.value)}
              rows={2}
              placeholder="Tell the agent what looks stale, wrong, or missing for this context node."
              className="min-h-16 text-xs"
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="self-start"
              onClick={requestCorrection}
              disabled={
                !project?.can_edit || !correctionText.trim() || correctionSaving
              }
            >
              {correctionSaving ? "Saving…" : "Request update"}
            </Button>
          </div>
        </section>
      </div>
      {dirty && (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-border bg-surface-subtle px-4 py-2 text-xs sm:px-6">
          <span className="mr-auto font-medium">Unsaved draft</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setTitle(baseline.title);
              setType(baseline.type);
              setContent(baseline.content);
              setSourceRefsText(baseline.sourceRefsText);
            }}
          >
            <X className="mr-1 h-3.5 w-3.5" />
            Revert
          </Button>
          <Button
            size="sm"
            onClick={save}
            disabled={
              !project?.can_edit ||
              saving ||
              Boolean(remoteUpdate) ||
              remoteDeleted
            }
          >
            <Save className="mr-1 h-3.5 w-3.5" />
            {saving ? "Saving…" : "Save"}
          </Button>
        </footer>
      )}
    </div>
  );
}

// ---- ProposalsSection ----------------------------------------------------

function ProposalsSection({
  nodeId,
  lastEvent,
  onAccepted,
}: {
  nodeId: number;
  lastEvent: SSEPayload | null;
  onAccepted: () => void;
}) {
  const { project } = useWorkspace();
  const [proposals, setProposals] = useState<KnowledgeProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const all = await listProposalsForNode(nodeId);
      setProposals(all.filter((p) => p.status === "PENDING"));
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Proposal loading failed.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [nodeId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (
      lastEvent?.action === "SYNC_REQUIRED" ||
      lastEvent?.entity === "knowledge_proposal"
    ) {
      void load();
    }
  }, [lastEvent]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleReview(id: number, action: "accept" | "reject") {
    setReviewing(id);
    setError(null);
    try {
      await reviewProposal(id, action);
      if (action === "accept") onAccepted();
      await load();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Proposal review failed.",
      );
    } finally {
      setReviewing(null);
    }
  }

  return (
    <section
      className="rounded-md border border-status-review-border bg-status-review p-3"
      aria-labelledby={`proposals-${nodeId}`}
    >
      <div className="mb-2 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-status-review-foreground" />
        <h3
          id={`proposals-${nodeId}`}
          className="text-xs font-semibold uppercase tracking-wider text-status-review-foreground"
        >
          Human review proposals
        </h3>
        {loading && (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
        )}
      </div>
      {error && (
        <div
          className="mb-2 flex items-center justify-between gap-2"
          role="alert"
        >
          <p className="text-xs text-destructive">{error}</p>
          <Button size="sm" variant="outline" onClick={() => void load()}>
            Retry
          </Button>
        </div>
      )}
      {!loading && !error && proposals.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No pending proposals. This node has no changes awaiting human review.
        </p>
      )}
      {proposals.map((p) => (
        <Surface
          key={p.id}
          tone="elevated"
          padding="sm"
          className="mb-2 text-xs last:mb-0"
        >
          <div className="mb-1 flex items-start justify-between gap-2">
            <div className="text-[10px] text-muted-foreground">
              <p className="font-medium text-foreground">
                Proposed by {p.proposed_by}
              </p>
              <p>
                <Clock className="mr-0.5 inline h-2.5 w-2.5" />
                {formatWhen(p.created_at)}
              </p>
            </div>
            <div className="flex flex-wrap justify-end gap-1">
              <Button
                size="sm"
                variant="outline"
                className="h-6 px-2 text-xs text-destructive hover:bg-destructive/20"
                disabled={!project?.can_edit || reviewing === p.id}
                onClick={() => void handleReview(p.id, "reject")}
              >
                <X className="mr-1 h-3 w-3" />
                Reject proposal
              </Button>
              <Button
                size="sm"
                className="h-6 px-2 text-[10px]"
                disabled={!project?.can_edit || reviewing === p.id}
                onClick={() => void handleReview(p.id, "accept")}
              >
                <Check className="mr-1 h-3 w-3" />
                Accept as human review
              </Button>
            </div>
          </div>
          {p.rationale && (
            <p className="mb-2 leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">Rationale: </span>
              {p.rationale}
            </p>
          )}
          <dl className="grid gap-2">
            {Object.entries(p.proposed_changes).map(([field, value]) => (
              <div
                key={field}
                className="grid gap-1 border-t border-border/60 pt-2 sm:grid-cols-[7rem_minmax(0,1fr)]"
              >
                <dt className="font-medium">{formatProposalField(field)}</dt>
                <dd className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground">
                  {formatProposalValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        </Surface>
      ))}
    </section>
  );
}

// ---- visuals --------------------------------------------------------------

const TYPE_BORDER: Record<KnowledgeNodeType, string> = {
  RAW: "border-muted-foreground/60",
  SUMMARY: "border-status-review-border",
  PRD: "border-primary",
  TDD: "border-status-done-border",
};

const TYPE_BADGE: Record<KnowledgeNodeType, string> = {
  RAW: "border-border bg-muted text-muted-foreground",
  SUMMARY:
    "border-status-review-border bg-status-review text-status-review-foreground",
  PRD: "border-primary/30 bg-primary/10 text-foreground",
  TDD: "border-status-done-border bg-status-done text-status-done-foreground",
};

function TypeBadge({ type }: { type: KnowledgeNodeType }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center justify-center rounded border px-1.5 text-[9px] font-semibold uppercase tracking-wider",
        TYPE_BADGE[type],
      )}
    >
      {type}
    </span>
  );
}

function KnowledgeStatusBadge({
  status,
  count,
  compact = false,
}: {
  status: KnowledgeNodeStatus;
  count?: number;
  compact?: boolean;
}) {
  const variant =
    status === "CURRENT" ? "done" : status === "STALE" ? "review" : "todo";
  const label =
    status === "CURRENT"
      ? "Current"
      : status === "STALE"
        ? "Stale"
        : "Archived";
  return (
    <Badge
      variant={variant}
      className={cn("gap-1", compact && "px-1 py-0 text-[9px]")}
      aria-label={count === undefined ? label : `${count} ${label} nodes`}
    >
      <span>{compact && status === "ARCHIVED" ? "Arch" : label}</span>
      {count !== undefined && <span>{count}</span>}
    </Badge>
  );
}

function useNarrowKnowledgeLayout() {
  const [narrow, setNarrow] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 767px)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return narrow;
}

interface KnowledgeDraft {
  title: string;
  type: KnowledgeNodeType;
  content: string;
  sourceRefsText: string;
  updatedAt: string;
}

function knowledgeDraft(node: KnowledgeNode): KnowledgeDraft {
  return {
    title: node.title,
    type: node.node_type,
    content: node.content,
    sourceRefsText: node.source_refs.join("\n"),
    updatedAt: node.updated_at,
  };
}

function applyKnowledgeDraft(
  node: KnowledgeNode,
  setters: {
    setTitle: (value: string) => void;
    setType: (value: KnowledgeNodeType) => void;
    setContent: (value: string) => void;
    setSourceRefsText: (value: string) => void;
    setBaseline: (value: KnowledgeDraft) => void;
  },
) {
  const next = knowledgeDraft(node);
  setters.setTitle(next.title);
  setters.setType(next.type);
  setters.setContent(next.content);
  setters.setSourceRefsText(next.sourceRefsText);
  setters.setBaseline(next);
}

function formatProposalField(field: string) {
  return field
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatProposalValue(value: unknown) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

const CONTENT_PLACEHOLDERS: Record<KnowledgeNodeType, string> = {
  RAW: "Paste the raw file excerpt, documentation, or research content here.",
  SUMMARY:
    "Write a compressed summary of this branch of the tree. Reference the children by their #id so a reviewer can drill down.",
  PRD: "# Product Requirements Document\n\n## Goal\n\n## User stories\n\n## Acceptance criteria",
  TDD: "# Technical Design Document\n\n## Architecture\n\n## Data model\n\n## Open questions",
};

function formatWhen(iso: string): string {
  try {
    const date = new Date(iso);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
