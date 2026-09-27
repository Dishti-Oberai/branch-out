import * as vscode from "vscode";
import * as api from "./api";
import { askAboutSelection } from "./quickPick";
import { openTreePanel } from "./treePanel";

export function activate(context: vscode.ExtensionContext): void {
  // Best-effort: declare the workspace root with the backend so
  // /apply-to-file has something to check paths against (Step 3.2). If the
  // backend isn't running yet, this just fails silently — apply-to-file
  // itself will surface a clear error at call time if the workspace was
  // never successfully declared.
  const folder = vscode.workspace.workspaceFolders?.[0];
  if (folder) {
    api.setWorkspace(folder.uri.fsPath).catch(() => {
      // Backend not up yet, or no workspace — commands below re-attempt
      // via their own error handling. Not fatal at activation.
    });
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("branchout.openTreePanel", () => openTreePanel(context)),
    vscode.commands.registerCommand("branchout.askAboutSelection", () => askAboutSelection())
  );
}

export function deactivate(): void {
  // Nothing to tear down: the extension holds no state of its own beyond
  // the webview panel, which VS Code disposes on its own.
}
