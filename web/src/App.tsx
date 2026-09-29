import { ProjectInvitationPage } from "@/components/ProjectInvitationPage";
import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { LandingPage } from "@/components/marketing/LandingPage";
import { MouvadahLockup } from "@/components/brand/mouvadah-brand";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { WorkspaceProvider } from "@/context/WorkspaceContext";

const AppLayout = lazy(() =>
  import("@/components/AppLayout").then((module) => ({
    default: module.AppLayout,
  })),
);
const LoginPage = lazy(() =>
  import("@/components/LoginPage").then((module) => ({
    default: module.LoginPage,
  })),
);
const ProfilePage = lazy(() =>
  import("@/components/ProfilePage").then((module) => ({
    default: module.ProfilePage,
  })),
);

const PROJECT_INVITATION_KEY = "mouvadah.pending-project-invitation";
function readProjectInvitation() {
  const token = new URLSearchParams(window.location.hash.slice(1)).get(
    "project_invite",
  );
  if (token) {
    sessionStorage.setItem(PROJECT_INVITATION_KEY, token);
    window.history.replaceState({}, "", `/app${window.location.search}`);
  }
  return token ?? sessionStorage.getItem(PROJECT_INVITATION_KEY);
}
const INVITATION_STORAGE_KEY = "mouvadah.pending-invitation";
type AppPath = "/" | "/app";

function readPendingInvitation(): string | null {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get("invite");

  if (token) {
    window.sessionStorage.setItem(INVITATION_STORAGE_KEY, token);
  }

  const pendingInvitation =
    token ?? window.sessionStorage.getItem(INVITATION_STORAGE_KEY);

  if (pendingInvitation && window.location.pathname !== "/app") {
    window.history.replaceState({}, "", `/app${window.location.search}`);
  } else if (token) {
    window.history.replaceState({}, "", `/app${window.location.search}`);
  }

  return pendingInvitation;
}

function readAppPath(): AppPath {
  return window.location.pathname === "/app" ? "/app" : "/";
}

function RouteLoading() {
  return (
    <main
      data-surface="marketing"
      className="flex min-h-screen items-center justify-center bg-background px-6 text-foreground"
    >
      <div className="motion-enter flex flex-col items-center gap-5 text-center">
        <MouvadahLockup size="lg" />
        <div className="space-y-2">
          <p className="technical-label">Resolving workspace state</p>
          <p className="text-sm text-muted-foreground">
            Checking the current browser session…
          </p>
        </div>
        <div className="h-px w-40 overflow-hidden bg-border" aria-hidden="true">
          <span className="motion-continuous block h-full w-1/2 animate-shimmer bg-brand-brass" />
        </div>
      </div>
    </main>
  );
}

function AppInner() {
  const { user, loading, logout } = useAuth();
  const [projectInvitation, setProjectInvitation] = useState(
    readProjectInvitation,
  );
  const [invitationToken, setInvitationToken] = useState<string | null>(
    readPendingInvitation,
  );
  const [path, setPath] = useState<AppPath>(readAppPath);
  const [view, setView] = useState<"workspace" | "profile">(
    invitationToken || window.location.hash === "#profile" ? "profile" : "workspace",
  );

  const navigate = useCallback((nextPath: AppPath, replace = false) => {
    const target = `${nextPath}${window.location.search}`;
    if (replace) {
      window.history.replaceState({}, "", target);
    } else {
      window.history.pushState({}, "", target);
    }
    setPath(nextPath);
  }, []);

  useEffect(() => {
    const onPopState = () => setPath(readAppPath());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!loading && user && path !== "/app") {
      navigate("/app", true);
    }
  }, [loading, navigate, path, user]);

  useEffect(() => {
    const className = "app-shell-active";
    const shellIsActive = Boolean(user) && path === "/app";
    document.documentElement.classList.toggle(className, shellIsActive);

    return () => {
      document.documentElement.classList.remove(className);
    };
  }, [path, user]);

  if (loading) {
    return <RouteLoading />;
  }

  if (user && path !== "/app") {
    return <RouteLoading />;
  }

  if (!user) {
    if (path === "/") {
      return <LandingPage onOpenApp={() => navigate("/app")} />;
    }
    return (
      <Suspense fallback={<RouteLoading />}>
        <LoginPage onBackToLanding={() => navigate("/")} />
      </Suspense>
    );
  }

  return (
    <WorkspaceProvider>
      <div
        data-testid="authenticated-app-shell"
        className="h-dvh w-full overflow-hidden overscroll-none bg-background"
      >
        <Suspense fallback={<RouteLoading />}>
          {projectInvitation ? (
            <ProjectInvitationPage
              token={projectInvitation}
              onDone={() => {
                sessionStorage.removeItem(PROJECT_INVITATION_KEY);
                setProjectInvitation(null);
              }}
            />
          ) : view === "profile" ? (
            <ProfilePage
              onBack={() => setView("workspace")}
              pendingInvitationToken={invitationToken}
              onInvitationHandled={() => {
                window.sessionStorage.removeItem(INVITATION_STORAGE_KEY);
                setInvitationToken(null);
              }}
              onInvitationTerminalFailure={() => {
                window.sessionStorage.removeItem(INVITATION_STORAGE_KEY);
              }}
              onInvitationSwitchAccount={async () => {
                if (invitationToken) {
                  window.sessionStorage.setItem(
                    INVITATION_STORAGE_KEY,
                    invitationToken,
                  );
                }
                await logout();
              }}
            />
          ) : (
            <AppLayout onNavigateProfile={() => setView("profile")} />
          )}
        </Suspense>
      </div>
    </WorkspaceProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  );
}
