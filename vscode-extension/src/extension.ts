import * as vscode from "vscode";
import * as api from "./api";
import { askAboutSelection } from "./quickPick";
import { openTreePanel } from "./treePanel";

// Phase 5.6: debounced reporting of the active editor/selection -- fires
// very often (every cursor move), so this is kept lightweight (file path +
// line numbers only, never the real text) and debounced rather than
// reporting on every single event.
const _REPORT_DEBOUNCE_MS = 300;
let _reportTimer: ReturnType<typeof setTimeout> | undefined;

function reportActiveEditor(editor: vscode.TextEditor | undefined): void {
  if (_reportTimer) {
    clearTimeout(_reportTimer);
  }
  _reportTimer = setTimeout(() => {
    // Real bug, found via manual testing (2026-10-17): focusing the Tree
    // Panel webview (or the terminal, output pane, etc.) makes
    // `vscode.window.activeTextEditor` undefined -- a webview is not a
    // text editor -- which used to report `null` and disable the "Code
    // change" intent exactly when the user had just opened the panel to
    // USE it. The active editor going undefined does NOT mean no file is
    // open; it means focus is on a non-editor surface. So when it's
    // undefined, fall back to a still-visible text editor and only report
    // `null` when there genuinely are none open at all.
    const effective = editor
      ?? vscode.window.visibleTextEditors.find((e) => e.document.uri.scheme === "file");
    if (!effective) {
      api.reportActiveEditorContext(null);
      return;
    }
    const selection = effective.selection;
    const targetRange = selection.isEmpty
      ? undefined
      : { start_line: selection.start.line + 1, end_line: selection.end.line + 1 };
    api.reportActiveEditorContext(effective.document.uri.fsPath, targetRange);
  }, _REPORT_DEBOUNCE_MS);
}

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

  // Report whatever's active right now, then keep reporting as it changes.
  reportActiveEditor(vscode.window.activeTextEditor);
  context.subscriptions.push(
    vscode.window.onDidChangeActiveTextEditor(reportActiveEditor),
    vscode.window.onDidChangeTextEditorSelection((e) => reportActiveEditor(e.textEditor)),
    vscode.commands.registerCommand("branchout.openTreePanel", () => openTreePanel(context)),
    vscode.commands.registerCommand("branchout.askAboutSelection", () => askAboutSelection())
  );
}

export function deactivate(): void {
  if (_reportTimer) {
    clearTimeout(_reportTimer);
  }
  // Real bug, found via manual testing (2026-10-17): this used to report
  // `null` here unconditionally, on the same reasoning as the comment
  // that used to be here -- "don't show a stale target after the window
  // closes". But the backend is a single process shared across every VS
  // Code window (same deliberate single-session POC scope that the
  // workspace-root fix elsewhere in this file already had to account
  // for) -- so closing or reloading ANY window, even an old one that
  // isn't the one actually being used, wiped out the CURRENTLY ACTIVE
  // window's real, correct state. Confirmed directly: killing a stale
  // dev-host window during a live test session cleared the live window's
  // active-editor-context to null, disabling the "Code change" intent
  // option and silently stripping target_file from every subsequent
  // request, with no error shown anywhere. No call here at all now --
  // same non-decision `_workspace_root` already makes on deactivate.
}
