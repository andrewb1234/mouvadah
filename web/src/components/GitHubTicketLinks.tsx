import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { createGitHubLink, listGitHubLinks, listGitHubRepositories, removeGitHubLink } from "@/lib/api";
import type { GitHubLink, GitHubRepository } from "@/lib/api";

export function GitHubTicketLinks({ ticketId, projectId }: { ticketId: number; projectId: number }) {
  const [links, setLinks] = useState<GitHubLink[]>([]);
  const [repos, setRepos] = useState<GitHubRepository[]>([]);
  const [repositoryId, setRepositoryId] = useState("");
  const [kind, setKind] = useState<"pull" | "issue">("pull");
  const [number, setNumber] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try { const [l, r] = await Promise.all([listGitHubLinks(ticketId), listGitHubRepositories(projectId)]); setLinks(l); setRepos(r); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not load GitHub links. Try refreshing."); }
    finally { setLoading(false); }
  }, [ticketId, projectId]);
  useEffect(() => { setLinks([]); setRepos([]); setError(""); void load(); }, [load]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await fn(); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not change GitHub link. Try again."); }
    finally { setBusy(false); }
  }
  const selectClass = "h-10 min-w-0 rounded-sm border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return <section aria-labelledby={`github-links-${ticketId}`} className="space-y-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 id={`github-links-${ticketId}`} className="text-sm font-semibold">GitHub evidence</h3><Button size="sm" variant="outline" disabled={busy || loading} onClick={() => { setError(""); void load(); }}>Refresh links</Button></div>
    <p className="text-xs text-muted-foreground">PR and issue state is last observed evidence. It does not change this ticket’s status or approve a release.</p>
    {loading && <p role="status" className="text-sm text-muted-foreground">Loading links…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    <ul className="divide-y divide-border">{links.map(link => <li key={link.id} className="space-y-1 py-3 text-sm">
      <div className="flex items-start justify-between gap-3"><a className="min-w-0 break-words underline underline-offset-4" href={link.snapshot.url} target="_blank" rel="noreferrer">{link.repository_name} · {link.kind === "pull" ? "PR" : "Issue"} #{link.number}: {link.snapshot.title ?? "Linked item"} ↗</a><Button variant="ghost" size="sm" disabled={busy} onClick={() => void act(() => removeGitHubLink(ticketId, link.id))} aria-label={`Remove ${link.repository_name} ${link.kind === "pull" ? "PR" : "issue"} ${link.number} link`}>Remove</Button></div>
      <p className="text-xs text-muted-foreground">{link.snapshot.state ?? "Unknown"} · {link.synced_at ? new Date(link.synced_at + "Z").toLocaleString() : "Not synced yet"}{link.snapshot.head_sha && <> · <code>{link.snapshot.head_sha.slice(0, 12)}</code></>}</p>
      {(link.connection_status !== "active" || link.repository_status !== "active") && <p className="text-xs text-destructive">Sync unavailable. This is historical evidence; ask an administrator to check GitHub access.</p>}
    </li>)}</ul>
    {!loading && !links.length && <p className="text-sm text-muted-foreground">No tracked pull requests or issues yet.</p>}
    {repos.length ? <form className="flex flex-col items-stretch gap-2 sm:flex-row sm:flex-wrap sm:items-end" onSubmit={e => { e.preventDefault(); void act(() => createGitHubLink(ticketId, Number(repositoryId), kind, Number(number))); }}>
      <label className="grid min-w-0 flex-1 gap-1 text-xs">Repository<select required className={selectClass} value={repositoryId} onChange={e => setRepositoryId(e.target.value)}><option value="">Choose repository</option>{repos.map(r => <option key={r.repository_id} value={r.repository_id} disabled={r.status !== "active"}>{r.full_name}</option>)}</select></label>
      <label className="grid gap-1 text-xs">Type<select className={selectClass} value={kind} onChange={e => setKind(e.target.value as "pull" | "issue")}><option value="pull">Pull request</option><option value="issue">Issue</option></select></label>
      <label className="grid w-24 gap-1 text-xs">Number<Input type="number" min="1" required value={number} onChange={e => setNumber(e.target.value)} /></label>
      <Button disabled={busy || !repositoryId || !number}>Track link</Button>
    </form> : !loading && <p className="text-xs text-muted-foreground">Ask a workspace administrator to map a repository in Profile → GitHub.</p>}
  </section>;
}
