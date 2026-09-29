/**
 * THESIS: Workspace administrators connect GitHub and map repositories explicitly.
 * OWN-WORLD: Inherit ProfilePage's restrained surfaces, borders and native forms.
 * STORY: Choose a workspace, authorize an installation, map a repo, inspect sync.
 * FIRST VIEWPORT: Heading and workspace selector precede connection state/actions.
 * FORM: A local settings extension; sequential forms and compact repository rows.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Github, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { connectGitHub, disconnectGitHub, getGitHubSettings, mapGitHubRepository, reconcileGitHub, unmapGitHubRepository } from "@/lib/api";
import type { GitHubSettings } from "@/lib/api";
import type { Project, Workspace } from "@/types";

const selectClass = "h-10 min-w-0 rounded-sm border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function GitHubSettingsSection({ workspaces, projects }: { workspaces: Workspace[]; projects: Project[] }) {
  const admins = workspaces.filter(w => ["OWNER", "ADMIN"].includes(w.role) && !w.deletion_requested_at);
  const [chosen, setChosen] = useState(() => new URLSearchParams(window.location.search).get("github_workspace") ?? "");
  const workspaceId = admins.some(w => String(w.id) === chosen) ? Number(chosen) : admins[0]?.id ?? 0;
  const requestSequence = useRef(0);
  const sectionRef = useRef<HTMLElement>(null);
  const [callbackOutcome, setCallbackOutcome] = useState(() => new URLSearchParams(window.location.search).get("github"));
  const [data, setData] = useState<GitHubSettings | null>(null);
  const [installation, setInstallation] = useState("");
  const [mapping, setMapping] = useState("");
  const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    if (!workspaceId) return;
    const sequence = ++requestSequence.current;
    setLoading(true);
    try { const result = await getGitHubSettings(workspaceId); if (sequence === requestSequence.current) setData(result); }
    catch (e) { if (sequence === requestSequence.current) setError(e instanceof Error ? e.message : "Could not load GitHub settings. Try refreshing."); }
    finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [workspaceId]);
  useEffect(() => { setData(null); setMapping(""); setProjectId(""); setError(""); setNotice(""); void load(); return () => { requestSequence.current++; }; }, [load]);
  useEffect(() => {
    if (!callbackOutcome) return;
    sectionRef.current?.focus();
    sectionRef.current?.scrollIntoView({ block: "start" });
    const url = new URL(window.location.href);
    url.searchParams.delete("github"); url.searchParams.delete("github_workspace");
    window.history.replaceState({}, "", url);
  }, [callbackOutcome]);
  async function act(fn: () => Promise<unknown>, message: string) {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); setNotice(message); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "GitHub action failed. Try again."); }
    finally { setBusy(false); }
  }
  const choices = data?.connections.filter(c => c.status === "active").flatMap(c => c.allowed_repositories.map(r => ({ ...r, installationId: c.installation_id }))) ?? [];
  const callbackMessages: Record<string, string> = {
    connected: "GitHub connected. Choose a repository and project below to start tracking work.",
    cancelled: "GitHub authorization was cancelled. Your connection was not changed. You can connect again when ready.",
    expired: "GitHub authorization expired or was already used. Start a new connection below.",
    unavailable: "GitHub is not configured on this server. Ask the server operator to finish setup.",
    access_changed: "Workspace access changed during authorization. Choose a workspace you administer and try again.",
    already_connected: "This installation is already assigned to a workspace. Reopen that workspace’s GitHub settings.",
    authorization_failed: "GitHub could not authorize this connection. Check the installation ID and that you administer its repositories, then connect again.",
  };
  return <section ref={sectionRef} tabIndex={-1} id="github-integration" aria-labelledby="github-heading" className="scroll-mt-6 space-y-4 focus:outline-none">
    <div><h2 id="github-heading" className="flex items-center gap-2 text-sm font-semibold"><Github className="h-4 w-4" aria-hidden />GitHub</h2>
      <p className="mt-1 text-xs text-muted-foreground">Connect repositories to projects and keep linked pull requests and issues up to date.</p></div>
    {callbackOutcome && callbackMessages[callbackOutcome] && <div role="status" className="rounded-sm border border-border p-3 text-sm">{callbackMessages[callbackOutcome]} <button className="ml-2 underline underline-offset-4" onClick={() => setCallbackOutcome(null)}>Dismiss</button></div>}
    {!admins.length ? <p className="text-sm text-muted-foreground">A workspace owner or administrator can connect GitHub.</p> : <>
      <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-end">
        <label className="grid min-w-0 flex-1 gap-1 text-xs">Workspace<select className={selectClass} value={workspaceId} disabled={busy} onChange={e => { requestSequence.current++; setData(null); setChosen(e.target.value); setCallbackOutcome(null); }}>{admins.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
        <Button variant="outline" disabled={busy || loading} onClick={() => { setError(""); void load(); }}><RefreshCw className="mr-2 h-4 w-4" aria-hidden />Refresh status</Button>
      </div>
      {loading && <p role="status" className="text-sm text-muted-foreground">Loading GitHub settings…</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {data && !data.enabled && <p className="rounded-sm border border-border bg-muted/30 p-3 text-sm">GitHub integration is not configured on this server. Ask the server operator to configure the GitHub App and sync worker.</p>}
      {data?.enabled && <>
        <div className="space-y-3 border-t border-border pt-4">
          <p className="text-sm">Install the app for selected repositories, then connect its installation. You must administer the repositories you share with this workspace.</p>
          {data.install_url && <a className="inline-block text-sm underline underline-offset-4" href={data.install_url} target="_blank" rel="noreferrer">Install or configure GitHub App ↗</a>}
          <form className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-end" onSubmit={e => { e.preventDefault(); void act(async () => { const result = await connectGitHub(workspaceId, Number(installation)); window.location.assign(result.authorization_url); }, "Opening GitHub authorization…"); }}>
            <label className="grid min-w-0 flex-1 gap-1 text-xs">Installation ID<Input required type="number" min="1" value={installation} onChange={e => setInstallation(e.target.value)} placeholder="From the GitHub installation settings URL" /></label>
            <Button disabled={busy || !installation}>Connect GitHub</Button>
          </form>
        </div>
        <div className="divide-y divide-border">{data.connections.map(c => <div key={c.installation_id} className="space-y-2 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="min-w-0 break-words text-sm font-medium">{c.account_login} <span className="font-normal text-muted-foreground">· {c.status}</span></p>
            <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" disabled={busy || c.status === "disconnected"} onClick={() => void act(() => reconcileGitHub(workspaceId, c.installation_id), "Sync queued. Refresh status after the next worker run.")}>Sync now</Button><Button size="sm" variant="outline" disabled={busy || c.status === "disconnected"} onClick={() => { if (window.confirm(`Disconnect ${c.account_login}? Sync will stop; existing evidence will remain available as history.`)) void act(() => disconnectGitHub(workspaceId, c.installation_id), "GitHub disconnected. To revoke the app itself, use GitHub installation settings."); }}>Disconnect</Button></div></div>
          <p className="text-xs text-muted-foreground">Last checked: {new Date(c.updated_at + "Z").toLocaleString()}</p>
          {c.last_error && <p className="text-sm text-destructive">Sync needs attention ({c.last_error.replaceAll("_", " ")}). Next attempt: {new Date(c.next_sync_at + "Z").toLocaleString()}. Reconnect if access changed.</p>}
        </div>)}</div>
        {choices.length > 0 && <form className="flex flex-col items-stretch gap-3 border-t border-border pt-4 sm:flex-row sm:items-end" onSubmit={e => { e.preventDefault(); const repo = choices.find(r => String(r.id) === mapping); if (repo) void act(() => mapGitHubRepository(workspaceId, repo.installationId, repo.id, Number(projectId)), "Repository mapped to project."); }}>
          <label className="grid min-w-0 flex-1 gap-1 text-xs">Repository<select required className={selectClass} value={mapping} onChange={e => setMapping(e.target.value)}><option value="">Choose repository</option>{choices.map(r => <option key={r.id} value={r.id}>{r.full_name}</option>)}</select></label>
          <label className="grid min-w-0 flex-1 gap-1 text-xs">Project<select required className={selectClass} value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">Choose project</option>{projects.filter(p => p.workspace_id === workspaceId).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <Button disabled={busy || !mapping || !projectId}>Map repository</Button>
        </form>}
        <ul className="divide-y divide-border">{data.repositories.map(r => <li key={r.repository_id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm">
          <div className="min-w-0"><p className="break-words font-medium">{r.full_name}</p><p className="text-xs text-muted-foreground">{projects.find(p => p.id === r.project_id)?.name ?? `Project #${r.project_id}`} · {r.status}</p></div>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => { if (window.confirm(`Unmap ${r.full_name}? This removes its tracked PR and issue links from Mouvadah.`)) void act(() => unmapGitHubRepository(workspaceId, r.repository_id), "Repository mapping removed."); }}>Unmap</Button>
        </li>)}</ul>
        {!data.connections.length && <p className="text-sm text-muted-foreground">No GitHub installations connected to this workspace yet.</p>}
      </>}
    </>}
  </section>;
}
