import { useWorkspace } from "@/context/WorkspaceContext";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Clock3,
  GitPullRequest,
  History,
  Link as LinkIcon,
  Link2,
  RefreshCw,
  Save,
  ShieldAlert,
} from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  ASSIGNEE_LABELS,
  BLOCKED_BY_LABELS,
  TICKET_STATUSES,
  TICKET_STATUS_LABELS,
} from "@/types";
import type {
  BlockedByCategory,
  TicketAssignee,
  TicketDetail,
  TicketStatus,
} from "@/types";
import { linkTicketMR, listProjectTickets, updateTicket } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAsync } from "@/hooks/useAsync";

const BLOCKED_BY_OPTIONS: BlockedByCategory[] = [
  "WAITING_HUMAN",
  "WAITING_DEPENDENCY",
  "AMBIGUOUS_REQUIREMENT",
  "EXTERNAL",
];

const ASSIGNEES: TicketAssignee[] = ["UNASSIGNED", "HUMAN", "AGENT"];

const statusTone: Record<TicketStatus, string> = {
  TODO: "bg-status-todo-border",
  IN_PROGRESS: "bg-status-progress-border",
  BLOCKED: "bg-status-blocked-border",
  REVIEW: "bg-status-review-border",
  DONE: "bg-status-done-border",
};

interface Props {
  ticket: TicketDetail;
  onChanged: () => void;
}

export function MetadataPane({ ticket, onChanged }: Props) {
  const { project } = useWorkspace();
  const [mrUrl, setMrUrl] = useState(ticket.mr_link ?? "");
  const [blockedReason, setBlockedReason] = useState(
    ticket.blocked_reason ?? "",
  );
  const [dependencyIds, setDependencyIds] = useState<number[]>(
    ticket.depends_on ?? [],
  );
  const dependencyBaseline = useRef({
    ticketId: ticket.id,
    ids: ticket.depends_on ?? [],
  });
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const projectTickets = useAsync(
    () =>
      ticket.project_id
        ? listProjectTickets(ticket.project_id)
        : Promise.resolve([]),
    [ticket.project_id],
  );

  useEffect(() => {
    setMrUrl(ticket.mr_link ?? "");
    setBlockedReason(ticket.blocked_reason ?? "");
  }, [ticket.id, ticket.mr_link, ticket.blocked_reason]);

  useEffect(() => {
    setDependencyIds((current) => {
      const previous = dependencyBaseline.current;
      const localDirty =
        current.length !== previous.ids.length ||
        current.some((id) => !previous.ids.includes(id));
      return ticket.id !== previous.ticketId || !localDirty
        ? [...ticket.depends_on]
        : current;
    });
    dependencyBaseline.current = {
      ticketId: ticket.id,
      ids: [...ticket.depends_on],
    };
  }, [ticket.id, ticket.depends_on]);

  const dependenciesDirty = useMemo(
    () =>
      dependencyIds.length !== ticket.depends_on.length ||
      dependencyIds.some((id) => !ticket.depends_on.includes(id)),
    [dependencyIds, ticket.depends_on],
  );

  async function runAction(name: string, action: () => Promise<unknown>) {
    setBusyAction(name);
    setActionError(null);
    try {
      await action();
      onChanged();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : `Failed to ${name}`,
      );
    } finally {
      setBusyAction(null);
    }
  }

  async function setStatus(status: TicketStatus) {
    const patch: Parameters<typeof updateTicket>[1] = { status };
    if (status === "BLOCKED" && !ticket.blocked_by) {
      patch.blocked_by = "WAITING_DEPENDENCY";
    }
    await runAction("change status", () => updateTicket(ticket.id, patch));
  }

  async function setBlockedBy(blockedBy: BlockedByCategory) {
    await runAction("change blocker", () =>
      updateTicket(ticket.id, {
        blocked_by: blockedBy,
        ...(ticket.status === "BLOCKED" ? {} : { status: "BLOCKED" }),
      }),
    );
  }

  async function saveDependencies() {
    await runAction("save dependencies", () =>
      updateTicket(ticket.id, { depends_on: dependencyIds }),
    );
  }

  function toggleDependency(id: number) {
    setDependencyIds((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id],
    );
  }

  async function saveBlockedReason() {
    await runAction("save blocked reason", () =>
      updateTicket(ticket.id, {
        blocked_reason: blockedReason.trim() || null,
      }),
    );
  }

  async function setAssignee(assignee: TicketAssignee) {
    await runAction("change assignee", () =>
      updateTicket(ticket.id, { assignee }),
    );
  }

  async function saveMR(event: React.FormEvent) {
    event.preventDefault();
    if (!mrUrl.trim()) return;
    await runAction("attach merge request", () =>
      linkTicketMR(ticket.id, mrUrl.trim()),
    );
  }

  const leaseExpiry = parseTimestamp(ticket.lease_expires_at);
  const leaseExpired =
    leaseExpiry != null && leaseExpiry.getTime() <= Date.now();
  const otherTickets =
    projectTickets.data?.filter((reference) => reference.id !== ticket.id) ??
    [];

  return (
    <div className="space-y-6 text-sm">
      <div>
        <h2 className="text-sm font-semibold tracking-tight">
          Execution metadata
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Explicit controls remain authoritative. Board columns update after the
          saved state is returned.
        </p>
      </div>

      {actionError && (
        <div
          role="alert"
          className="flex items-start gap-2 border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          <span className="min-w-0 flex-1">{actionError}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setActionError(null)}
          >
            Dismiss
          </Button>
        </div>
      )}

      <MetadataSection title="State and ownership">
        <div>
          <label className="text-xs font-semibold">Status</label>
          <Select
            value={ticket.status}
            onValueChange={(value) => void setStatus(value as TicketStatus)}
            disabled={!project?.can_edit || busyAction != null}
          >
            <SelectTrigger
              className="mt-1 min-h-11 sm:min-h-9"
              aria-label="Ticket status"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TICKET_STATUSES.map((status) => (
                <SelectItem key={status} value={status}>
                  {TICKET_STATUS_LABELS[status]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <label className="text-xs font-semibold">Assignee</label>
          <Select
            value={ticket.assignee}
            onValueChange={(value) => void setAssignee(value as TicketAssignee)}
            disabled={!project?.can_edit || busyAction != null}
          >
            <SelectTrigger
              className="mt-1 min-h-11 sm:min-h-9"
              aria-label="Ticket assignee"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ASSIGNEES.map((assignee) => (
                <SelectItem key={assignee} value={assignee}>
                  {ASSIGNEE_LABELS[assignee]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div>
          <label className="text-xs font-semibold">Blocked category</label>
          <Select
            value={ticket.status === "BLOCKED" ? (ticket.blocked_by ?? "") : ""}
            onValueChange={(value) =>
              void setBlockedBy(value as BlockedByCategory)
            }
            disabled={!project?.can_edit || busyAction != null}
          >
            <SelectTrigger
              className="mt-1 min-h-11 sm:min-h-9"
              aria-label="Ticket blocked category"
            >
              <SelectValue placeholder="Mark as blocked…" />
            </SelectTrigger>
            <SelectContent>
              {BLOCKED_BY_OPTIONS.map((option) => (
                <SelectItem key={option} value={option}>
                  {BLOCKED_BY_LABELS[option]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {ticket.status === "BLOCKED" && (
            <div className="mt-2">
              <label
                htmlFor={`ticket-${ticket.id}-blocked-reason`}
                className="sr-only"
              >
                Blocked reason
              </label>
              <Input
                readOnly={!project?.can_edit}
                id={`ticket-${ticket.id}-blocked-reason`}
                value={blockedReason}
                onChange={(event) => setBlockedReason(event.target.value)}
                placeholder="Optional reason…"
                className="min-h-11 text-xs sm:min-h-9"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2 w-full"
                onClick={() => void saveBlockedReason()}
                disabled={
                  !project?.can_edit ||
                  busyAction != null ||
                  blockedReason === (ticket.blocked_reason ?? "")
                }
              >
                <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
                Save blocked reason
              </Button>
            </div>
          )}
        </div>
      </MetadataSection>

      <MetadataSection title="Claim and lease">
        {ticket.claimed_by ? (
          <div
            className={cn(
              "border p-3 text-xs",
              leaseExpired
                ? "border-status-blocked-border bg-status-blocked text-status-blocked-foreground"
                : "border-status-progress-border bg-status-progress text-status-progress-foreground",
            )}
          >
            <div className="flex items-center gap-2 font-semibold">
              {leaseExpired ? (
                <ShieldAlert className="h-4 w-4" aria-hidden />
              ) : (
                <Bot className="h-4 w-4" aria-hidden />
              )}
              {leaseExpired ? "Lease expired" : "Agent claim active"}
            </div>
            <dl className="mt-3 grid gap-2">
              <div>
                <dt className="font-mono text-[11px] uppercase opacity-70">
                  Worker
                </dt>
                <dd className="break-all font-mono">{ticket.claimed_by}</dd>
              </div>
              <div>
                <dt className="font-mono text-[11px] uppercase opacity-70">
                  Claimed
                </dt>
                <dd>{formatTimestamp(ticket.claimed_at)}</dd>
              </div>
              <div>
                <dt className="font-mono text-[11px] uppercase opacity-70">
                  Lease expiry
                </dt>
                <dd>{formatTimestamp(ticket.lease_expires_at)}</dd>
              </div>
            </dl>
          </div>
        ) : (
          <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">
            No worker currently owns this ticket. Claims are created and renewed
            by agent coordination clients, not this UI.
          </p>
        )}
      </MetadataSection>

      <MetadataSection title={`Dependencies (${dependencyIds.length})`}>
        <div className="max-h-56 space-y-1 overflow-y-auto border border-border p-1.5">
          {projectTickets.loading && !projectTickets.data && (
            <p
              role="status"
              className="px-2 py-2 text-xs text-muted-foreground"
            >
              Loading project tickets…
            </p>
          )}
          {projectTickets.error && (
            <div className="p-2 text-xs">
              <p role="alert" className="text-destructive">
                {projectTickets.error.message}
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="mt-2"
                onClick={() => projectTickets.refetch()}
              >
                <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
                Retry
              </Button>
            </div>
          )}
          {otherTickets.map((reference) => (
            <label
              key={reference.id}
              className="flex min-h-11 cursor-pointer items-center gap-2 px-2 py-1 text-xs hover:bg-accent sm:min-h-9"
            >
              <input
                type="checkbox"
                checked={dependencyIds.includes(reference.id)}
                onChange={() => toggleDependency(reference.id)}
              />
              {reference.status === "DONE" ? (
                <CheckCircle2
                  className="h-3.5 w-3.5 shrink-0 text-status-done-foreground"
                  aria-hidden
                />
              ) : (
                <span
                  className={cn(
                    "h-2.5 w-2.5 shrink-0 rounded-full",
                    statusTone[reference.status],
                  )}
                  aria-hidden
                />
              )}
              <span className="font-mono text-muted-foreground">
                #{reference.id}
              </span>
              <span className="min-w-0 flex-1 truncate" title={reference.title}>
                {reference.title}
              </span>
              <span className="sr-only">
                {TICKET_STATUS_LABELS[reference.status]}
              </span>
              {reference.subproject_name && (
                <span
                  className="max-w-24 shrink-0 truncate text-[11px] text-muted-foreground"
                  title={reference.subproject_name}
                >
                  {reference.subproject_name}
                </span>
              )}
            </label>
          ))}
          {otherTickets.length === 0 &&
            !projectTickets.loading &&
            !projectTickets.error && (
              <p className="px-2 py-3 text-xs text-muted-foreground">
                No other tickets in this project.
              </p>
            )}
        </div>
        <Button
          className="mt-2 w-full"
          size="sm"
          variant="outline"
          onClick={() => void saveDependencies()}
          disabled={
            !project?.can_edit || busyAction != null || !dependenciesDirty
          }
        >
          <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
          {busyAction === "save dependencies" ? "Saving…" : "Save dependencies"}
        </Button>
      </MetadataSection>

      <MetadataSection title="Merge request">
        <form onSubmit={saveMR}>
          <label htmlFor={`ticket-${ticket.id}-mr`} className="sr-only">
            Merge request URL
          </label>
          <div className="flex items-center gap-2">
            <Input
              readOnly={!project?.can_edit}
              id={`ticket-${ticket.id}-mr`}
              value={mrUrl}
              onChange={(event) => setMrUrl(event.target.value)}
              placeholder="https://github.com/org/repo/pull/123"
              className="min-h-11 min-w-0 text-xs sm:min-h-9"
            />
            <Button
              type="submit"
              size="icon"
              variant="outline"
              className="h-11 w-11 shrink-0 sm:h-9 sm:w-9"
              disabled={
                !project?.can_edit || busyAction != null || !mrUrl.trim()
              }
              aria-label="Attach MR"
            >
              <GitPullRequest className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        </form>
        {ticket.mr_link ? (
          <a
            href={ticket.mr_link}
            target="_blank"
            rel="noopener noreferrer"
            className="focus-ring mt-2 flex min-h-11 items-center gap-1.5 break-all text-xs font-semibold text-foreground underline decoration-brand-brass underline-offset-2"
          >
            <LinkIcon className="h-3.5 w-3.5 shrink-0" aria-hidden />
            Open linked merge request
          </a>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            No merge request is attached.
          </p>
        )}
      </MetadataSection>

      <MetadataSection title="Evidence and provenance">
        {ticket.source_refs.length > 0 ? (
          <ul className="space-y-2">
            {ticket.source_refs.map((reference) => (
              <li
                key={reference}
                className="flex min-w-0 items-start gap-2 border border-border bg-background/60 p-2 text-xs"
              >
                <Link2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <code className="min-w-0 break-all">{reference}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">
            No source or evidence references are attached.
          </p>
        )}
      </MetadataSection>

      <MetadataSection title="Audit ledger">
        {ticket.audit_logs.length > 0 ? (
          <ol className="space-y-2 text-xs">
            {ticket.audit_logs
              .slice()
              .reverse()
              .slice(0, 12)
              .map((log) => (
                <li
                  key={log.id}
                  className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 border-l border-border pl-2"
                >
                  <History
                    className="mt-0.5 h-3.5 w-3.5 text-muted-foreground"
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <strong>
                      {log.actor_name
                        ? `${log.actor_name}${log.actor === "AGENT" ? "’s agent" : ""}`
                        : log.actor}
                    </strong>{" "}
                    {log.action.replaceAll("_", " ").toLowerCase()}
                    <time
                      className="mt-0.5 block font-mono text-[11px] text-muted-foreground"
                      dateTime={log.timestamp}
                    >
                      {formatTimestamp(log.timestamp)}
                    </time>
                  </span>
                </li>
              ))}
          </ol>
        ) : (
          <p className="border border-dashed border-border p-3 text-xs text-muted-foreground">
            No audit events have been recorded for this ticket.
          </p>
        )}
      </MetadataSection>

      {busyAction && (
        <p
          role="status"
          className="flex items-center gap-2 text-xs text-muted-foreground"
        >
          <Clock3 className="h-3.5 w-3.5" aria-hidden />
          Updating {busyAction}…
        </p>
      )}
    </div>
  );
}

function MetadataSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3 border-t border-border pt-4">
      <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
      {children}
    </section>
  );
}

function formatTimestamp(value?: string | null) {
  if (!value) return "Not available";
  const date = parseTimestamp(value);
  if (!date) return "Not available";
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function parseTimestamp(value?: string | null) {
  if (!value) return null;
  const normalized = value.endsWith("Z") ? value : `${value}Z`;
  return new Date(normalized);
}
