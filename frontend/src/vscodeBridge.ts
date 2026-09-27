// Step 3.5: detects whether this app is running inside the VS Code
// extension's webview (treePanel.ts) rather than a standalone browser tab.
// VS Code injects `acquireVsCodeApi` into every webview's global scope;
// nothing else does. Used only to decide whether to show IDE-specific UI
// (the "Apply to file" action) — the app works identically either way
// otherwise, same backend, same components.
export function isVsCodeWebview(): boolean {
  return typeof (window as unknown as { acquireVsCodeApi?: unknown }).acquireVsCodeApi === "function";
}
