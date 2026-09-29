import { useWorkspace } from "@/context/WorkspaceContext";
import { useCallback, useMemo, useState } from "react";
import { AlertCircle, Loader2, Plus, X } from "lucide-react";
import { TicketCard } from "@/components/TicketCard";
import { TicketStatusIndicator } from "@/components/ui/state-indicator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createTicket, deleteTicket } from "@/lib/api";
import type {
  SSEPayload,
  SubprojectDetail,
  Ticket,
  TicketAssignee,
  TicketStatus,
} from "@/types";
import {
  ASSIGNEE_LABELS,
  TICKET_STATUSES,
  TICKET_STATUS_LABELS,
} from "@/types";

interface Props {
  subproject: SubprojectDetail;
  onTicketClick: (ticketId: number) => void;
  onSubprojectRefetch: () => void;
  lastEvent: SSEPayload | null;
}

export function KanbanBoard({
  subproject,
  onTicketClick,
  onSubprojectRefetch,
}: Props) {
  const [actionError, setActionError] = useState<string | null>(null);

  const ticketsByStatus = useMemo(() => {
    const grouped: Record<TicketStatus, Ticket[]> = {
      TODO: [],
      IN_PROGRESS: [],
      BLOCKED: [],
      REVIEW: [],
      DONE: [],
    };
    for (const ticket of subproject.tickets)
      grouped[ticket.status].push(ticket);
    return grouped;
  }, [subproject.tickets]);

  const handleDelete = useCallback(
    async (ticket: Ticket) => {
      if (!window.confirm(`Delete ticket "${ticket.title}"?`)) return;
      setActionError(null);
      try {
        await deleteTicket(ticket.id);
        onSubprojectRefetch();
      } catch (error) {
        setActionError(
          error instanceof Error ? error.message : "Failed to delete ticket",
        );
      }
    },
    [onSubprojectRefetch],
  );

  return (
    <section
      aria-label="Execution board"
      className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
    >
      {actionError && (
        <div
          role="alert"
          className="mx-4 mt-3 flex items-center gap-2 border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">{actionError}</span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={() => setActionError(null)}
            aria-label="Dismiss board error"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      )}
      <div
        data-testid="kanban-scroll"
        className="flex min-h-0 min-w-0 w-full max-w-full flex-1 snap-x snap-mandatory gap-3 overflow-x-auto overscroll-x-contain px-4 py-4 [contain:layout_paint] sm:snap-none"
      >
        {TICKET_STATUSES.map((status) => (
          <KanbanColumn
            key={status}
            status={status}
            tickets={ticketsByStatus[status]}
            subprojectId={subproject.id}
            onTicketClick={onTicketClick}
            onTicketDelete={handleDelete}
            onCreated={onSubprojectRefetch}
          />
        ))}
      </div>
    </section>
  );
}

interface ColumnProps {
  status: TicketStatus;
  tickets: Ticket[];
  subprojectId: number;
  onTicketClick: (ticketId: number) => void;
  onTicketDelete: (ticket: Ticket) => void;
  onCreated: () => void;
}

function KanbanColumn({
  status,
  tickets,
  subprojectId,
  onTicketClick,
  onTicketDelete,
  onCreated,
}: ColumnProps) {
  const { project } = useWorkspace();
  const [creating, setCreating] = useState(false);

  return (
    <section
      data-testid={`column-${status}`}
      data-status={status}
      aria-labelledby={`column-${status}-heading`}
      className="kanban-column flex h-full w-[calc(100vw-2rem)] max-w-80 shrink-0 snap-center flex-col overflow-hidden border border-border bg-surface sm:w-80"
    >
      <header className="flex min-h-12 items-center justify-between border-b border-border bg-surface-subtle/50 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <h3 id={`column-${status}-heading`} className="sr-only">
            {TICKET_STATUS_LABELS[status]}
          </h3>
          <TicketStatusIndicator status={status} className="text-[11px]" />
          <span
            className="font-mono text-[11px] text-muted-foreground"
            aria-label={`${tickets.length} tickets`}
          >
            {String(tickets.length).padStart(2, "0")}
          </span>
        </div>
        {project?.can_edit && status === "TODO" && (
          <Button
            size="icon"
            variant="ghost"
            className="h-11 w-11 sm:h-8 sm:w-8"
            onClick={() => setCreating((value) => !value)}
            aria-label={creating ? "Cancel new ticket" : "New ticket"}
            aria-expanded={creating}
          >
            {creating ? (
              <X className="h-4 w-4" aria-hidden />
            ) : (
              <Plus className="h-4 w-4" aria-hidden />
            )}
          </Button>
        )}
      </header>
      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-2">
        {creating && (
          <NewTicketForm
            subprojectId={subprojectId}
            onClose={() => setCreating(false)}
            onCreated={() => {
              onCreated();
              setCreating(false);
            }}
          />
        )}
        {tickets.map((ticket) => (
          <TicketCard
            key={ticket.id}
            ticket={ticket}
            onClick={() => onTicketClick(ticket.id)}
            onDelete={
              project?.can_edit ? () => onTicketDelete(ticket) : undefined
            }
          />
        ))}
        {tickets.length === 0 && !creating && (
          <div className="border border-dashed border-border px-3 py-8 text-center">
            <p className="text-xs font-medium text-muted-foreground">
              No {TICKET_STATUS_LABELS[status].toLowerCase()} work
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              Status changes appear here after they are saved.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function NewTicketForm({
  subprojectId,
  onClose,
  onCreated,
}: {
  subprojectId: number;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [assignee, setAssignee] = useState<TicketAssignee>("UNASSIGNED");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createTicket(subprojectId, {
        title: title.trim(),
        description: description.trim() || undefined,
        assignee,
      });
      onCreated();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Failed to create ticket",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="space-y-3 border border-brand-brass/40 bg-card p-3 text-xs"
    >
      <div>
        <label htmlFor="new-ticket-title" className="technical-label">
          Work item
        </label>
        <Input
          id="new-ticket-title"
          autoFocus
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Ticket title"
          className="mt-1 min-h-11 text-xs sm:min-h-9"
          aria-invalid={error ? true : undefined}
        />
      </div>
      <div>
        <label htmlFor="new-ticket-description" className="technical-label">
          Outcome and constraints
        </label>
        <Textarea
          id="new-ticket-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="Description"
          className="mt-1 min-h-20 text-xs"
        />
      </div>
      <div>
        <label className="technical-label">Owner</label>
        <Select
          value={assignee}
          onValueChange={(value) => setAssignee(value as TicketAssignee)}
        >
          <SelectTrigger
            className="mt-1 min-h-11 text-xs sm:min-h-9"
            aria-label="New ticket assignee"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(["UNASSIGNED", "HUMAN", "AGENT"] as TicketAssignee[]).map(
              (role) => (
                <SelectItem key={role} value={role}>
                  {ASSIGNEE_LABELS[role]}
                </SelectItem>
              ),
            )}
          </SelectContent>
        </Select>
      </div>
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm" disabled={saving || !title.trim()}>
          {saving ? (
            <>
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
              Creating…
            </>
          ) : (
            "Create ticket"
          )}
        </Button>
      </div>
    </form>
  );
}
