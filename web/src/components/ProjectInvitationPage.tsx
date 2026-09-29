import { useState } from "react";
import { LockKeyhole } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAsync, clearAsyncCache } from "@/hooks/useAsync";
import { useAuth } from "@/context/AuthContext";
import { useWorkspace } from "@/context/WorkspaceContext";
import { acceptProjectInvitation, previewProjectInvitation } from "@/lib/api";

export function ProjectInvitationPage({
  token,
  onDone,
}: {
  token: string;
  onDone: () => void;
}) {
  const { user, logout } = useAuth();
  const { setActiveProjectId } = useWorkspace();
  const invitation = useAsync(() => previewProjectInvitation(token), [token]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function accept() {
    setBusy(true);
    setError(null);
    try {
      const project = await acceptProjectInvitation(token);
      clearAsyncCache();
      setActiveProjectId(project.id, project.name);
      onDone();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not accept invitation.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="flex h-full items-center justify-center overflow-auto p-6">
      <section className="w-full max-w-lg">
        <LockKeyhole className="mb-5 h-8 w-8 text-muted-foreground" />
        <p className="mb-3 break-all text-sm text-muted-foreground">
          Signed in as {user?.email}
        </p>
        {invitation.loading ? (
          <p role="status">Checking invitation…</p>
        ) : invitation.error ? (
          <>
            <h1 className="text-2xl font-semibold">Invitation unavailable</h1>
            <p role="alert" className="mt-4 text-sm text-muted-foreground">
              This invitation may have expired, been used, or been revoked. You
              must sign in with the email it was created for. Ask the project
              owner for a new invitation if needed.
            </p>
            <Button className="mt-6" onClick={() => void logout()}>
              Switch account
            </Button>
          </>
        ) : (
          invitation.data && (
            <>
              <h1 className="text-2xl font-semibold">
                Join {invitation.data.project_name}
              </h1>
              <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
                {invitation.data.inviter_name} invited you to{" "}
                {invitation.data.role === "EDITOR" ? "view and edit" : "view"}{" "}
                this project in {invitation.data.workspace_name}, including its
                tickets, knowledge, discussion, and existing history. Your own
                workspace stays separate.
              </p>
              <Button
                className="mt-6"
                disabled={busy}
                onClick={() => void accept()}
              >
                {busy ? "Joining…" : "Accept invitation"}
              </Button>
            </>
          )
        )}
        {error && (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {error}
          </p>
        )}
        <Button variant="ghost" className="ml-2 mt-6" onClick={onDone}>
          Back to my projects
        </Button>
      </section>
    </main>
  );
}
