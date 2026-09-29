import { useWorkspace } from "@/context/WorkspaceContext";
import { useEffect, useRef, useState } from "react";
import { AlertCircle, Loader2, RefreshCw, Save, Trash2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { TechnicalLabel } from "@/components/ui/technical-label";
import {
  ApiError,
  deleteTicket,
  getProject,
  getTicket,
  updateTicket,
} from "@/lib/api";
import { useAsync } from "@/hooks/useAsync";
import { cn } from "@/lib/utils";
import type { SSEPayload, TicketDetail } from "@/types";
import { CommentThread } from "@/components/CommentThread";
import { MetadataPane } from "@/components/MetadataPane";

interface Props {
  ticketId: number | null;
  onClose: () => void;
  lastEvent: SSEPayload | null;
}

export function TicketModal({ ticketId, onClose, lastEvent }: Props) {
  const { activeProjectId, refreshAccess } = useWorkspace();
  const isOpen = ticketId != null;
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [remoteUpdatePending, setRemoteUpdatePending] = useState(false);
  const ticket = useAsync<TicketDetail | null>(
    async () => {
      if (ticketId == null) return null;
      try {
        return await getTicket(ticketId);
      } catch (error) {
        if (
          error instanceof ApiError &&
          error.status === 404 &&
          activeProjectId != null
        ) {
          // Retain a deleted ticket only while its enclosing project is still authorized.
          try {
            await getProject(activeProjectId);
          } catch (accessError) {
            refreshAccess();
            throw accessError;
          }
        }
        throw error;
      }
    },
    [ticketId, activeProjectId],
    { retainOnError: true },
  );

  useEffect(() => {
    setHasUnsavedChanges(false);
    setRemoteUpdatePending(false);
  }, [ticketId]);

  useEffect(() => {
    if (!lastEvent || ticketId == null) return;
    const concernsTicket =
      lastEvent.action === "SYNC_REQUIRED" ||
      (lastEvent.entity === "ticket" && lastEvent.entity_id === ticketId);
    const concernsComments =
      lastEvent.entity === "comment" && lastEvent.parent_id === ticketId;

    if (concernsComments) {
      ticket.refetch();
      return;
    }
    if (concernsTicket) {
      if (hasUnsavedChanges) setRemoteUpdatePending(true);
      else ticket.refetch();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastEvent, ticketId, hasUnsavedChanges]);

  function requestClose() {
    if (
      hasUnsavedChanges &&
      !window.confirm("Discard unsaved ticket title or description changes?")
    ) {
      return;
    }
    onClose();
  }

  return (
    <Dialog open={isOpen} onOpenChange={(next) => !next && requestClose()}>
      <DialogContent
        className="left-0 top-0 h-dvh w-screen max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden border-0 p-0 sm:left-1/2 sm:top-1/2 sm:h-[min(90dvh,56rem)] sm:w-[calc(100%-2rem)] sm:max-w-6xl sm:-translate-x-1/2 sm:-translate-y-1/2 sm:border"
        onOpenAutoFocus={() => {
          if (document.activeElement instanceof HTMLElement) {
            returnFocusRef.current = document.activeElement;
          }
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          returnFocusRef.current?.focus();
          returnFocusRef.current = null;
        }}
      >
        <DialogTitle className="sr-only">
          {ticket.data?.title ??
            (ticketId == null ? "Ticket detail" : `Ticket #${ticketId}`)}
        </DialogTitle>
        <DialogDescription className="sr-only">
          Edit work content, discussion, execution metadata, provenance, and
          claim state.
        </DialogDescription>
        {!ticket.data && ticket.loading && (
          <div
            role="status"
            className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"
          >
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
            Loading ticket…
          </div>
        )}
        {!ticket.data && ticket.error && (
          <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
            <AlertCircle className="h-6 w-6 text-destructive" aria-hidden />
            <div>
              <p className="text-sm font-semibold">
                Ticket could not be loaded
              </p>
              <p role="alert" className="mt-1 text-xs text-muted-foreground">
                {ticket.error.message}
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={() => ticket.refetch()}
            >
              <RefreshCw className="mr-2 h-4 w-4" aria-hidden />
              Retry
            </Button>
          </div>
        )}
        {ticket.data && (
          <TicketBody
            ticket={ticket.data}
            refreshing={ticket.loading}
            refetchError={ticket.error?.message ?? null}
            remoteDeleted={
              ticket.error instanceof ApiError && ticket.error.status === 404
            }
            remoteUpdatePending={remoteUpdatePending}
            onDirtyChange={setHasUnsavedChanges}
            onRefresh={() => ticket.refetch()}
            onSaved={() => {
              setRemoteUpdatePending(false);
              ticket.refetch();
            }}
            onClose={onClose}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function TicketBody({
  ticket,
  refreshing,
  refetchError,
  remoteDeleted,
  remoteUpdatePending,
  onDirtyChange,
  onRefresh,
  onSaved,
  onClose,
}: {
  ticket: TicketDetail;
  refreshing: boolean;
  refetchError: string | null;
  remoteDeleted: boolean;
  remoteUpdatePending: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onRefresh: () => void;
  onSaved: () => void;
  onClose: () => void;
}) {
  const { project } = useWorkspace();
  const [title, setTitle] = useState(ticket.title);
  const [description, setDescription] = useState(ticket.description ?? "");
  const [baseline, setBaseline] = useState({
    ticketId: ticket.id,
    title: ticket.title,
    description: ticket.description ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const dirty =
    title !== baseline.title || description !== baseline.description;

  useEffect(() => {
    const next = {
      ticketId: ticket.id,
      title: ticket.title,
      description: ticket.description ?? "",
    };
    const localDirty =
      title !== baseline.title || description !== baseline.description;
    if (ticket.id !== baseline.ticketId || !localDirty) {
      setTitle(next.title);
      setDescription(next.description);
    }
    setBaseline(next);
    // The baseline deliberately represents the last server revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticket.id, ticket.title, ticket.description]);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  async function saveContent() {
    setSaving(true);
    setActionError(null);
    try {
      await updateTicket(ticket.id, {
        title: title.trim() || ticket.title,
        description,
      });
      onSaved();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Failed to save ticket",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete ticket "${ticket.title}"?`)) return;
    setActionError(null);
    try {
      await deleteTicket(ticket.id);
      onClose();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Failed to delete ticket",
      );
    }
  }

  return (
    <div
      data-testid="ticket-detail-layout"
      className="h-full min-h-0 overflow-y-auto lg:grid lg:grid-cols-[minmax(0,1fr)_21rem] lg:overflow-hidden"
    >
      <div
        data-testid="ticket-primary-pane"
        className="flex min-w-0 flex-col lg:min-h-0 lg:border-r lg:border-border"
      >
        <DialogHeader className="sticky top-0 z-10 border-b border-border bg-card px-4 pb-4 pt-4 pr-14 sm:px-6 sm:pt-6">
          <div className="flex flex-wrap items-center gap-2">
            <TechnicalLabel>Ticket #{ticket.id}</TechnicalLabel>
            {dirty && (
              <span
                role="status"
                className="border border-status-review-border bg-status-review px-2 py-0.5 text-xs font-semibold text-status-review-foreground"
              >
                Unsaved changes
              </span>
            )}
            {refreshing && (
              <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                Refreshing
              </span>
            )}
          </div>
          <label
            htmlFor={`ticket-${ticket.id}-title`}
            className="mt-3 font-mono text-[11px] font-semibold text-muted-foreground"
          >
            Ticket title
          </label>
          <Input
            readOnly={!project?.can_edit}
            id={`ticket-${ticket.id}-title`}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="h-auto min-h-11 rounded-none border-x-0 border-t-0 bg-transparent px-0 py-1 text-xl font-semibold shadow-none transition-fast hover:border-input focus-visible:border-brand-brass focus-visible:ring-0 sm:text-2xl"
          />
        </DialogHeader>

        <div className="flex flex-col gap-6 px-4 py-5 sm:px-6 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
          {(remoteUpdatePending || refetchError) && (
            <div
              role={refetchError ? "alert" : "status"}
              className={cn(
                "flex flex-col gap-3 border p-3 text-xs sm:flex-row sm:items-center",
                remoteDeleted
                  ? "border-destructive/40 bg-destructive/10"
                  : "border-warning/40 bg-warning/10",
              )}
            >
              <div className="min-w-0 flex-1">
                <p className="font-semibold">
                  {remoteDeleted
                    ? "This ticket was deleted elsewhere"
                    : refetchError
                      ? "Latest ticket state could not be refreshed"
                      : "A remote ticket update is available"}
                </p>
                <p className="mt-1 text-muted-foreground">
                  {remoteDeleted
                    ? "The safely loaded content remains visible for reference. Close this dialog to return to the updated board."
                    : refetchError
                      ? refetchError
                      : "Your local title and description remain intact. Save them before loading the remote revision."}
                </p>
              </div>
              {remoteDeleted ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={onClose}
                >
                  Close deleted ticket
                </Button>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={onRefresh}
                  disabled={dirty}
                >
                  <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
                  Refresh latest
                </Button>
              )}
            </div>
          )}

          {actionError && (
            <p
              role="alert"
              className="flex items-center gap-2 border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive"
            >
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {actionError}
            </p>
          )}

          <section aria-labelledby={`ticket-${ticket.id}-content-heading`}>
            <h3
              id={`ticket-${ticket.id}-content-heading`}
              className="text-sm font-semibold tracking-tight"
            >
              Work content
            </h3>
            <label
              htmlFor={`ticket-${ticket.id}-description`}
              className="mt-3 block text-xs font-semibold"
            >
              Description
            </label>
            <Textarea
              readOnly={!project?.can_edit}
              id={`ticket-${ticket.id}-description`}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={8}
              className="mt-1 min-h-44 leading-relaxed"
              placeholder="Describe the outcome, constraints, and acceptance evidence."
            />
            <div className="sticky bottom-0 mt-3 flex items-center justify-end border-t border-border bg-card/95 py-3 backdrop-blur">
              <Button
                size="sm"
                disabled={
                  !project?.can_edit || !dirty || saving || !title.trim()
                }
                onClick={() => void saveContent()}
              >
                <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
                {saving ? "Saving…" : "Save work content"}
              </Button>
            </div>
          </section>

          <section
            aria-labelledby={`ticket-${ticket.id}-discussion-heading`}
            className="min-h-80"
          >
            <CommentThread
              ticketId={ticket.id}
              comments={ticket.comments}
              onPosted={onRefresh}
              headingId={`ticket-${ticket.id}-discussion-heading`}
            />
          </section>

          <section
            aria-labelledby={`ticket-${ticket.id}-danger-heading`}
            className="border border-destructive/30 bg-destructive/5 p-4"
          >
            <h3
              id={`ticket-${ticket.id}-danger-heading`}
              className="text-sm font-semibold text-destructive"
            >
              Destructive action
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Deleting this ticket also removes its comments, audit history, and
              dependency edges. This cannot be undone.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-3 border-destructive/40 text-destructive hover:bg-destructive/10"
              disabled={!project?.can_edit}
              onClick={() => void handleDelete()}
            >
              <Trash2 className="mr-1 h-3.5 w-3.5" aria-hidden />
              Delete ticket
            </Button>
          </section>
        </div>
      </div>

      <aside
        data-testid="ticket-metadata-pane"
        aria-label="Execution metadata and provenance"
        className="min-w-0 border-t border-border bg-surface-subtle/35 p-4 sm:p-6 lg:overflow-y-auto lg:border-t-0"
      >
        <MetadataPane ticket={ticket} onChanged={onRefresh} />
      </aside>
    </div>
  );
}
