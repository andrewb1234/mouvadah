import { useState } from "react";
import { Menu } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { Workspace } from "@/components/Workspace";
import { TicketModal } from "@/components/TicketModal";
import { MouvadahLockup } from "@/components/brand/mouvadah-brand";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { ResizableSplit } from "@/components/ui/resizable-split";
import { useWorkspace } from "@/context/WorkspaceContext";
import { useSSE } from "@/hooks/useSSE";
import type { SSEPayload } from "@/types";

export function AppLayout({
  onNavigateProfile,
}: {
  onNavigateProfile: () => void;
}) {
  const {
    activeTicketId,
    openTicket,
    refreshAccess,
    revalidateAccess,
    activeProjectId,
    accessRevision,
    project,
  } = useWorkspace();
  const [lastEvent, setLastEvent] = useState<SSEPayload | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(false);

  useSSE((payload) => {
    setLastEvent(payload);
    if (
      (payload.action === "PROJECT_ACCESS_CHANGED" ||
        payload.action === "PROJECT_DELETED") &&
      payload.entity_id === activeProjectId
    )
      refreshAccess();
    else if (payload.action === "SYNC_REQUIRED") void revalidateAccess();
  });

  return (
    <div className="flex h-dvh w-full min-w-0 overflow-hidden bg-background">
      <a
        href="#workspace-main"
        className="focus-ring fixed left-4 top-4 z-[100] -translate-y-24 rounded-sm bg-primary px-4 py-3 text-sm font-semibold text-primary-foreground transition-transform focus:translate-y-0"
      >
        Skip to workspace
      </a>
      <ResizableSplit
        direction="horizontal"
        defaultSize={288}
        minSize={220}
        maxSize={520}
        storageKey="taskable.sidebar.width"
        separatorLabel="Resize workspace navigation"
        collapseFirstBelowMd
        first={
          <Sidebar
            lastEvent={lastEvent}
            onNavigateProfile={onNavigateProfile}
          />
        }
        second={
          <div className="flex h-full min-w-0 flex-1 flex-col">
            <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card px-4 md:hidden">
              <MouvadahLockup size="sm" />
              <Dialog open={navigationOpen} onOpenChange={setNavigationOpen}>
                <DialogTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="Open workspace navigation"
                  >
                    <Menu className="h-4 w-4" />
                  </Button>
                </DialogTrigger>
                <DialogContent className="left-0 top-0 flex h-dvh w-[min(88vw,340px)] max-w-none translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-y-0 border-l-0 p-0">
                  <DialogTitle className="sr-only">
                    Workspace navigation
                  </DialogTitle>
                  <DialogDescription className="sr-only">
                    Select a project or subproject, open profile settings, or
                    sign out.
                  </DialogDescription>
                  <Sidebar
                    lastEvent={lastEvent}
                    onNavigate={() => setNavigationOpen(false)}
                    onNavigateProfile={onNavigateProfile}
                  />
                </DialogContent>
              </Dialog>
            </header>
            <Workspace lastEvent={lastEvent} />
          </div>
        }
      />

      <TicketModal
        key={accessRevision}
        ticketId={project ? activeTicketId : null}
        onClose={() => openTicket(null)}
        lastEvent={lastEvent}
      />
    </div>
  );
}
