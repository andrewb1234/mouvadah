# Project collaboration visual QA

- Source visual truth: option 3, `/Users/andrewbetbadal/.codex/generated_images/01a0ec91-7765-7201-a3d3-6eaebe09cb7c/exec-73f87f69-43c7-44fc-8b00-cdd663039845.png` (1487 × 1058 image).
- Implementation screenshots: `output/project-collaboration/people-desktop.png` (1440 × 1000 CSS viewport), `output/project-collaboration/people-mobile.png` (390 × 844 CSS viewport).
- State: authenticated workspace owner, Platform → People, one inherited administrator, one direct editor, one pending viewer invitation. All screenshot identities/content are synthetic localhost fixtures.
- The source is a concept image rather than a measured CSS layout. Compare composition at matching desktop density, not pixel equality. Browser screenshots use CSS dimensions at 1×; the app's incumbent branding, navigation, and tokens remain authoritative. No new visual system was introduced.

## Findings and comparison history

The source and desktop implementation were opened together in the same comparison tool input. Both use a dedicated People page, inline email/role/invitation action, source-aware access rows, pending invitations, and a privacy footer. Existing brand/header components are intentionally retained. Additional copy distinguishes a project URL from an invitation and explains history, expiry, and account-bound acceptance. A personal agent-connection section follows sharing controls.

Initial narrow-screen inspection found clipped view names. Stacked icon/label navigation fixed this. The independent `impeccable_finish_reviewer` then found three material issues: focus-triggered refresh discarded drafts and one-time secrets; denied ticket reads could retain content; and 10px mobile tab labels were too small. Background access revalidation now preserves mounted state unless access changes, 401/403 clear retained data, and a ticket 404 checks its project before retaining deleted-ticket history. Tab labels are now 12px and wrap when necessary.

Post-fix mobile capture confirms all view names are legible and contained. Desktop capture confirms the access controls, source labels, pending invitations, and privacy explanation remain visible in the intended hierarchy. Full-view comparisons were sufficient; no separate asset/crop region required inspection because the page introduces no illustration assets. No actionable P0/P1/P2 visual findings remain.

## Interaction evidence

- Full browser regression: 38 passed before finish-review fixes.
- Post-fix focused browser regression: 7 passed, covering Knowledge editing/drafts/mobile, ticket CRUD/deleted-ticket recovery/mobile, and real two-account collaboration.
- Collaboration test covers invitation creation and acceptance, token removal from the URL, independent private projects, hidden admin controls, live editor-to-viewer downgrade, 360px containment, live removal, refresh after removal, and retained access to the guest's own project.
- One-time invitation link survives a browser focus event.
- Browser test fixture rejects unexpected console errors; the final focused run passed. A temporary Vite HMR context error during development was resolved by a fresh navigation and did not reproduce in the final tests.
- Production frontend build passes.

User approved the rendered desktop and mobile visuals on 2026-09-29. Pending invitations show email only; the accepted collaborator name comes from the authenticated profile, never inference from an email address. Jordan Lee in the screenshots is a synthetic accepted account.

Final result: passed
