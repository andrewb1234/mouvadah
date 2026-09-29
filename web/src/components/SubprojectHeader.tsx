import { useEffect, useState } from "react";
import { AlertCircle, Pencil, Save, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
import { useWorkspace } from "@/context/WorkspaceContext";
import { deleteSubproject, updateSubproject } from "@/lib/api";
import type { Subproject, SubprojectStatus } from "@/types";

const STATUSES: SubprojectStatus[] = ["PLANNING", "ACTIVE", "COMPLETED"];

interface Props {
  subproject: Subproject;
  onSaved: () => void;
  onDeleted?: () => void;
}

export function SubprojectHeader({ subproject, onSaved, onDeleted }: Props) {
  const { setActiveSubprojectId, project } = useWorkspace();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(subproject.name);
  const [brief, setBrief] = useState(subproject.context_brief);
  const [status, setStatus] = useState<SubprojectStatus>(subproject.status);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (editing) return;
    setName(subproject.name);
    setBrief(subproject.context_brief);
    setStatus(subproject.status);
  }, [editing, subproject]);

  function reset() {
    setName(subproject.name);
    setBrief(subproject.context_brief);
    setStatus(subproject.status);
    setError(null);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await updateSubproject(subproject.id, {
        name: name.trim() || subproject.name,
        context_brief: brief,
        status,
      });
      setEditing(false);
      onSaved();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Failed to save subproject context",
      );
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (
      !window.confirm(
        `Delete subproject "${subproject.name}" and all its tickets?`,
      )
    ) {
      return;
    }
    setError(null);
    try {
      await deleteSubproject(subproject.id);
      setActiveSubprojectId(null);
      onDeleted?.();
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Failed to delete subproject",
      );
    }
  }

  return (
    <section
      aria-labelledby="execution-context-heading"
      className="h-full overflow-auto border-b border-border bg-card/50 px-4 py-4 sm:px-6"
    >
      {!editing ? (
        <div className="flex min-w-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <h2
                id="execution-context-heading"
                className="min-w-0 truncate text-xl font-semibold tracking-tight"
                title={subproject.name}
              >
                {subproject.name}
              </h2>
              <Badge
                variant="outline"
                className="font-mono text-[11px] uppercase"
              >
                {subproject.status}
              </Badge>
            </div>
            <p className="mt-2 max-w-4xl whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
              {subproject.context_brief.trim() ||
                "No context brief yet. Add the outcome and constraints agents should load before work begins."}
            </p>
          </div>
          {project?.can_edit && (
            <div className="flex shrink-0 items-center gap-1 self-end sm:self-start">
              <Button
                variant="ghost"
                size="icon"
                className="h-11 w-11 sm:h-9 sm:w-9"
                onClick={() => {
                  reset();
                  setEditing(true);
                }}
                aria-label="Edit subproject"
              >
                <Pencil className="h-4 w-4" aria-hidden />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-11 w-11 text-destructive hover:bg-destructive/10 sm:h-9 sm:w-9"
                onClick={() => void remove()}
                aria-label="Delete subproject"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          <h3 className="text-sm font-semibold tracking-tight">
            Edit execution context
          </h3>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_11rem]">
            <div>
              <label htmlFor="subproject-name" className="technical-label">
                Name
              </label>
              <Input
                id="subproject-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-1 min-h-11 text-base font-semibold sm:min-h-9"
              />
            </div>
            <div>
              <label className="technical-label">Lifecycle</label>
              <Select
                value={status}
                onValueChange={(value) => setStatus(value as SubprojectStatus)}
              >
                <SelectTrigger
                  className="mt-1 min-h-11 sm:min-h-9"
                  aria-label="Subproject status"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUSES.map((value) => (
                    <SelectItem key={value} value={value}>
                      {value}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <label htmlFor="subproject-brief" className="technical-label">
              Agent orientation brief
            </label>
            <Textarea
              id="subproject-brief"
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              rows={4}
              className="mt-1"
              placeholder="Outcome, scope, constraints, and acceptance evidence."
            />
          </div>
          {error && (
            <p
              role="alert"
              className="flex items-center gap-2 text-xs text-destructive"
            >
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {error}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                reset();
                setEditing(false);
              }}
            >
              <X className="mr-1 h-3.5 w-3.5" aria-hidden />
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => void save()}
              disabled={saving || !name.trim()}
            >
              <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
              {saving ? "Saving…" : "Save context"}
            </Button>
          </div>
        </div>
      )}
      {!editing && error && (
        <p
          role="alert"
          className="mt-3 flex items-center gap-2 text-xs text-destructive"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {error}
        </p>
      )}
    </section>
  );
}
