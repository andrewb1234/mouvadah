import { useWorkspace } from "@/context/WorkspaceContext";
import { useState } from "react";
import { AlertCircle, Bot, Send, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { createComment } from "@/lib/api";
import type { Comment } from "@/types";

interface Props {
  ticketId: number;
  comments: Comment[];
  onPosted: () => void;
  headingId?: string;
}

export function CommentThread({
  ticketId,
  comments,
  onPosted,
  headingId,
}: Props) {
  const { project } = useWorkspace();
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!content.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createComment(ticketId, {
        author: "HUMAN",
        content: content.trim(),
      });
      setContent("");
      onPosted();
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Failed to post comment",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3">
        <h3 id={headingId} className="text-sm font-semibold tracking-tight">
          Human + agent discussion
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Decisions and implementation handoffs stay attached to this work item.
        </p>
      </div>
      <ol
        aria-label="Ticket comments"
        className="min-h-32 flex-1 space-y-3 overflow-y-auto pr-1"
      >
        {comments.length === 0 && (
          <li className="border border-dashed border-border p-4 text-xs text-muted-foreground">
            No discussion yet. Human and agent comments will appear here in
            chronological order.
          </li>
        )}
        {comments.map((comment) => (
          <li key={comment.id} className="flex gap-3">
            <div
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center border",
                comment.author === "AGENT"
                  ? "border-actor-agent-border bg-actor-agent text-actor-agent-foreground"
                  : "border-actor-human-border bg-actor-human text-actor-human-foreground",
              )}
              aria-hidden
            >
              {comment.author === "AGENT" ? (
                <Bot className="h-3.5 w-3.5" />
              ) : (
                <User className="h-3.5 w-3.5" />
              )}
            </div>
            <div className="min-w-0 flex-1 border border-border bg-background/70 px-3 py-2 text-xs">
              <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
                <strong>
                  {comment.actor_name
                    ? `${comment.actor_name}${comment.author === "AGENT" ? "’s agent" : ""}`
                    : comment.author === "AGENT"
                      ? "Agent"
                      : "Human"}
                </strong>
                <time
                  className="font-mono text-[11px] text-muted-foreground"
                  dateTime={comment.timestamp}
                >
                  {new Date(`${comment.timestamp}Z`).toLocaleString()}
                </time>
              </div>
              <p className="whitespace-pre-wrap leading-relaxed">
                {comment.content}
              </p>
            </div>
          </li>
        ))}
      </ol>

      {project?.can_edit && (
        <form onSubmit={submit} className="mt-4">
          <label
            htmlFor={`ticket-${ticketId}-comment`}
            className="text-xs font-semibold"
          >
            Add a human comment
          </label>
          <div className="mt-1 flex items-end gap-2">
            <Textarea
              id={`ticket-${ticketId}-comment`}
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="Write a comment as Human…"
              rows={3}
              className="min-h-20"
              aria-describedby={`ticket-${ticketId}-comment-help`}
              aria-invalid={error ? true : undefined}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  void submit(event);
                }
              }}
            />
            <Button
              type="submit"
              size="icon"
              className="h-11 w-11 shrink-0"
              disabled={saving || !content.trim()}
              aria-label={saving ? "Posting comment" : "Post comment"}
            >
              <Send className="h-4 w-4" aria-hidden />
            </Button>
          </div>
          <p
            id={`ticket-${ticketId}-comment-help`}
            className="mt-1 text-[11px] text-muted-foreground"
          >
            Press {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"} + Enter to
            post.
          </p>
          {error && (
            <p
              role="alert"
              className="mt-2 flex items-center gap-2 text-xs text-destructive"
            >
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
