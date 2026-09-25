import { useState } from "react";
import { Check, Copy, Terminal } from "lucide-react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface McpSetupModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  apiKey: string | null;
  initialMode?: "hosted" | "local";
}

const HOSTED_MCP_URL = "https://mouvadah.com/mcp";
const PROVIDERS = [
  { id: "claude", label: "Claude Desktop", file: "~/Library/Application Support/Claude/claude_desktop_config.json" },
  { id: "windsurf", label: "Windsurf", file: "~/.codeium/windsurf/mcp_config.json" },
  { id: "cursor", label: "Cursor", file: "~/.cursor/mcp.json" },
  { id: "vscode", label: "VS Code", file: "User mcp.json (MCP: Open User Configuration)" },
] as const;

export function McpSetupModal({ open, onOpenChange, apiKey, initialMode = "hosted" }: McpSetupModalProps) {
  const [mode, setMode] = useState<"hosted" | "local">(initialMode);
  const [provider, setProvider] = useState<string>("claude");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const apiUrl = `${window.location.origin}/api/v1`;
  const entry = {
    command: "mouvadah-mcp",
    args: [],
    env: { MOUVADAH_API_URL: apiUrl, MOUVADAH_API_KEY: apiKey ?? "YOUR_API_KEY" },
  };
  const config = JSON.stringify(provider === "vscode"
    ? { servers: { mouvadah: { type: "stdio", ...entry } } }
    : { mcpServers: { mouvadah: entry } }, null, 2);
  const activeProvider = PROVIDERS.find((p) => p.id === provider)!;

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopyError(false);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setCopyError(true);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-1rem)] w-[calc(100%-1rem)] max-w-2xl overflow-y-auto rounded-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Terminal className="h-5 w-5" />Connect an AI assistant</DialogTitle>
          <DialogDescription>Use the hosted connector from Claude on web or mobile, or install a local MCP bridge for a desktop client.</DialogDescription>
        </DialogHeader>
        <div className="space-y-5 px-6 pb-6">
          <div className="flex flex-wrap gap-2" aria-label="Connection method">
            {([['hosted', 'Hosted connector'], ['local', 'Local MCP bridge']] as const).map(([id, label]) => (
              <button key={id} type="button" aria-pressed={mode === id}
                onClick={() => { setMode(id); setCopied(false); setCopyError(false); }}
                className={cn("focus-ring min-h-10 rounded-sm px-3 py-2 text-sm font-medium", mode === id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>{label}</button>
            ))}
          </div>
          {mode === "hosted" ? (
            <div className="space-y-4 text-sm">
              <h3 className="font-semibold">Claude on web, desktop, and mobile</h3>
              <p className="text-muted-foreground">No download, local server, or API key is needed. Sign in to Mouvadah when Claude asks you to connect.</p>
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border bg-muted/50 p-3">
                <code className="break-all">{HOSTED_MCP_URL}</code>
                <Button variant="outline" size="sm" onClick={() => void copy(HOSTED_MCP_URL)}>{copied ? <Check className="mr-2 h-4 w-4" /> : <Copy className="mr-2 h-4 w-4" />}{copied ? "Copied" : "Copy connector URL"}</Button>
              </div>
              <ol className="ml-5 list-decimal space-y-3 text-muted-foreground">
                <li>Open <a className="underline" href="https://claude.ai/settings/connectors" target="_blank" rel="noreferrer">Claude Settings → Connectors</a> in a browser and choose <strong>Add custom connector</strong>. If your mobile app offers this option, you can start there too.</li>
                <li>Name it Mouvadah and paste the connector URL above. Leave advanced client ID and client secret fields empty.</li>
                <li>Choose <strong>Connect</strong>, sign in to your own Mouvadah account, select the workspace and access level, then allow the connection.</li>
                <li>Open the Claude mobile app with the same Claude account. Enable Mouvadah in your chat’s connectors/tools and ask: “List my Mouvadah projects.”</li>
              </ol>
              <p className="text-xs text-muted-foreground">To use a shared workspace, accept its invitation in Mouvadah before connecting. You can revoke a connection below under API Keys (named “MCP: …”). Reconnect after its 30-day authorization expires. Claude organization settings may restrict custom connectors.</p>
            </div>
          ) : (
            <div className="space-y-4 text-sm">
              <h3 className="font-semibold">Run the MCP bridge on your computer</h3>
              <p className="text-muted-foreground">For desktop clients that launch a local process. The bridge connects to this Mouvadah server at <code className="break-all">{apiUrl}</code>. Create an API key on this server and install <code>pipx install mouvadah-mcp</code> or <code>uv tool install mouvadah-mcp</code>.</p>
              <div className="flex flex-wrap gap-2">
                {PROVIDERS.map((p) => <button key={p.id} type="button" aria-pressed={provider === p.id} onClick={() => { setProvider(p.id); setCopied(false); }} className={cn("focus-ring min-h-10 rounded-sm px-3 py-1.5 text-xs font-medium", provider === p.id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>{p.label}</button>)}
              </div>
              <p className="text-xs text-muted-foreground">Config location: <code className="break-all">{activeProvider.file}</code></p>
              <pre className="max-h-64 overflow-auto rounded-sm border border-border bg-muted/50 p-4 text-xs"><code>{config}</code></pre>
              <Button variant="outline" size="sm" onClick={() => void copy(config)} disabled={!apiKey}>{copied ? "Configuration copied" : "Copy MCP configuration"}</Button>
              {!apiKey && <p className="text-xs text-muted-foreground">Create an API key below, then choose “Configure local MCP with this key” to include it in the configuration.</p>}
              <p className="text-xs text-muted-foreground">Save the configuration with owner-only permissions, restart {activeProvider.label}, and ask it to list your Mouvadah projects. This file contains a secret; revoke the key if it is exposed.</p>
              <div className="border-t border-border pt-4">
                <h4 className="font-semibold">Want to run all of Mouvadah locally?</h4>
                <p className="mt-2 text-muted-foreground">Follow the <a className="underline" href="https://github.com/andrewb1234/mouvadah#run-mouvadah-locally">Community installation instructions</a>, then open Settings in your local app and use its API key and API URL. Hosted keys do not grant access to a separate local installation.</p>
              </div>
            </div>
          )}
          {copyError && <p role="alert" className="text-xs text-destructive">Clipboard access was denied. Select the text and copy it manually.</p>}
        </div>
      </DialogContent>
    </Dialog>
  );
}
