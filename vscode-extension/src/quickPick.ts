import * as vscode from "vscode";
import * as api from "./api";

/**
 * Step 3.4: "Branch-Out: Ask about selection" — a fast, mostly-inline entry
 * point that doesn't require the tree panel to be open. Reads the active
 * file + selection as context, asks Just-answer vs Explore-3, shows the
 * result(s) in a QuickPick, and applies the chosen one straight to the file.
 *
 * Nodes created this way always set target_file/target_range (per Step
 * 3.1/3.2) — that's what distinguishes IDE-originated nodes from ones made
 * in the standalone web app, which never set them.
 *
 * Step 3.8 (2026-09-27, post-incident fix): earlier this only sent
 * target_file/target_range as *metadata* — the LLM never actually saw the
 * selected/file text, so "summarize this" style prompts had nothing real to
 * work from and could hallucinate destructive output onto files that had
 * nothing to do with the request (this is what happened to README.md). The
 * fix: read the real selection/file text with editor.document.getText()
 * and fold it directly into the prompt sent to the backend, since /branch
 * and /continue only accept a single `prompt` string. Also added the
 * "include whole codebase?" choice per the Phase 1/3 amendment, wired to
 * the now-implemented `include_codebase` flag.
 *
 * 2026-10-17 amendment: the question/code-change picker gained a default
 * third option, "Auto-detect" — the backend's own model classification
 * (`orchestrator._resolve_target`) decides whether the result is treated
 * as a question or a code change, instead of this client pre-composing a
 * forced "this is a question" steering string. "Ask a question"/"Change
 * the code" remain as explicit overrides for when you already know which
 * one you want, exactly as before. The steering text itself now lives
 * server-side (`branch_generator.py`), not here.
 */
export async function askAboutSelection(): Promise<void> {
  // Re-declare this window's folder before anything else — see api.ts's
  // ensureWorkspaceDeclared for why activation-time-only declaration isn't
  // enough (2026-09-27 fix, real bug: a second/earlier window can "win" the
  // shared backend's single declared root).
  await api.ensureWorkspaceDeclared();

  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showErrorMessage("Branch-Out: no active editor.");
    return;
  }

  const userPrompt = await vscode.window.showInputBox({
    prompt: "What do you want to ask or change?",
    placeHolder: "e.g. add error handling to this function",
  });
  if (!userPrompt) {
    return; // cancelled
  }

  const mode = await vscode.window.showQuickPick(
    [
      { label: "Auto-detect", detail: "Branch-Out decides — applies only if it's actually a code change", value: "auto" as const },
      { label: "Ask a question", detail: "Explain only — never touches your file, regardless of what Branch-Out thinks", value: "question" as const },
      { label: "Change the code", detail: "Always apply to the file, regardless of what Branch-Out thinks", value: "continue" as const },
      { label: "Explore 3 approaches", detail: "Branch into 3 alternatives to apply", value: "branch" as const },
    ],
    { placeHolder: "How should Branch-Out respond?" }
  );
  if (!mode) {
    return; // cancelled
  }

  const codebaseChoice = await vscode.window.showQuickPick(
    [
      {
        label: "Just this file",
        detail: "Faster, cheaper — use only the selection/file below",
        value: false,
      },
      {
        label: "Include the whole codebase",
        detail: "Slower, more tokens — lets Branch-Out see other files too",
        value: true,
      },
    ],
    { placeHolder: "How much context should Branch-Out use?" }
  );
  if (!codebaseChoice) {
    return; // cancelled
  }
  const includeCodebase = codebaseChoice.value;

  const targetFile = editor.document.uri.fsPath;
  const selection = editor.selection;
  const targetRange = selection.isEmpty
    ? undefined
    : { start_line: selection.start.line + 1, end_line: selection.end.line + 1 };

  // The actual fix: pull real text out of the editor, not just path/line
  // metadata, and fold it into the prompt so the LLM has real content to
  // work from.
  const selectedText = selection.isEmpty ? "" : editor.document.getText(selection);
  const fileText = editor.document.getText();
  const relativePath = vscode.workspace.asRelativePath(editor.document.uri);

  const fileSection = selectedText
    ? `File: ${relativePath}\n\nSelected code:\n\`\`\`\n${selectedText}\n\`\`\``
    : `File: ${relativePath}\n\nFull file contents:\n\`\`\`\n${fileText}\n\`\`\``;

  const composedPrompt = `${fileSection}\n\nRequest: ${userPrompt}`;

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Branch-Out is thinking…" },
    async () => {
      try {
        const { nodes } = await api.getSession();
        const active = api.activeNode(nodes);
        if (!active) {
          vscode.window.showErrorMessage("Branch-Out: couldn't find an active node — is the backend running?");
          return;
        }

        if (mode.value === "question") {
          // Explicit override: no target_file/range sent at all, and the
          // backend forces "question" framing regardless of what the
          // model would have classified — the node can never be applied
          // to a file, from here or from the tree panel's Apply button.
          const { node } = await api.continueFrom(
            active.id, composedPrompt, undefined, undefined, includeCodebase, "question"
          );
          const answer = extractAnswer(node.context_summary, node.code_snapshot);
          const doc = await vscode.workspace.openTextDocument({ content: answer, language: "markdown" });
          await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside, preview: true });
        } else if (mode.value === "auto") {
          // The backend decides, based on the model's own classification
          // (see orchestrator._resolve_target) -- target_file comes back
          // set on the node only if it was actually treated as a code
          // change, so that's exactly what we check here: no separate
          // "is_code_change" flag to read, the resolved target_file IS
          // the decision.
          const { node } = await api.continueFrom(
            active.id, composedPrompt, targetFile, targetRange, includeCodebase, "auto"
          );
          if (node.target_file) {
            await offerToApply(node.id, node.code_snapshot, "Apply this change to the file?");
          } else {
            const answer = extractAnswer(node.context_summary, node.code_snapshot);
            const doc = await vscode.workspace.openTextDocument({ content: answer, language: "markdown" });
            await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside, preview: true });
          }
        } else if (mode.value === "continue") {
          // Explicit override: target_file is forced through regardless
          // of what the model would have classified.
          const { node } = await api.continueFrom(
            active.id, composedPrompt, targetFile, targetRange, includeCodebase, "code_change"
          );
          await offerToApply(node.id, node.code_snapshot, "Apply this answer to the file?");
        } else {
          const { branches } = await api.branch(
            active.id, composedPrompt, 3, targetFile, targetRange, includeCodebase, "code_change"
          );
          if (branches.length === 0) {
            vscode.window.showWarningMessage("Branch-Out: no branches were generated.");
            return;
          }
          const picked = await vscode.window.showQuickPick(
            branches.map((b) => ({
              label: b.reasoning.slice(0, 80) || "(no reasoning given)",
              detail: b.code.split("\n").slice(0, 2).join(" ⏎ ").slice(0, 100),
              nodeId: b.node_id,
              code: b.code,
            })),
            { placeHolder: `Choose one of ${branches.length} approaches` }
          );
          if (picked) {
            await offerToApply(picked.nodeId, picked.code, "Apply this approach to the file?");
          }
        }
      } catch (e) {
        vscode.window.showErrorMessage(`Branch-Out: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
  );
}

async function offerToApply(nodeId: string, code: string, promptText: string): Promise<void> {
  const preview = code.length > 300 ? `${code.slice(0, 300)}…` : code;
  const choice = await vscode.window.showInformationMessage(
    `${promptText}\n\n${preview}`,
    { modal: true },
    "Apply"
  );
  if (choice !== "Apply") {
    return;
  }
  try {
    const { path: written } = await api.applyToFile(nodeId);
    vscode.window.showInformationMessage(`Branch-Out: applied to ${written}`);
  } catch (e) {
    vscode.window.showErrorMessage(`Branch-Out: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/** The answer lives in the latest "Approach:" line of context_summary; fall
 *  back to code_snapshot when the model's reply wasn't parseable JSON. */
function extractAnswer(contextSummary: string, codeSnapshot: string): string {
  const idx = contextSummary.lastIndexOf("Approach:");
  const approach = idx >= 0 ? contextSummary.slice(idx + "Approach:".length).trim() : "";
  if (approach && approach !== "(unparsed model output)") {
    return approach;
  }
  return codeSnapshot;
}
