import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import * as api from "./api";

/**
 * Step 3.3: "Branch-Out: Open Tree Panel" — a webview hosting the existing
 * React app unmodified (same Vite build as the standalone web app). It
 * talks to the same local FastAPI backend over plain fetch/WebSocket, which
 * webviews are allowed to do to localhost.
 *
 * The frontend must already be built (`npm run build` in frontend/) —
 * this reads frontend/dist/index.html and rewrites its asset references to
 * webview-resource URIs; it doesn't bundle or fork the React app.
 */

let panel: vscode.WebviewPanel | undefined;

export async function openTreePanel(context: vscode.ExtensionContext): Promise<void> {
  // Re-declare this window's folder before opening — see
  // api.ts's ensureWorkspaceDeclared (2026-09-27 fix).
  await api.ensureWorkspaceDeclared();

  if (panel) {
    panel.reveal(vscode.ViewColumn.Beside);
    return;
  }

  const distDir = path.join(context.extensionPath, "..", "frontend", "dist");
  const indexPath = path.join(distDir, "index.html");

  if (!fs.existsSync(indexPath)) {
    vscode.window.showErrorMessage(
      `Branch-Out: frontend isn't built yet. Run "npm run build" in frontend/ first (looked for ${indexPath}).`
    );
    return;
  }

  panel = vscode.window.createWebviewPanel(
    "branchoutTree",
    "Branch-Out",
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.file(distDir)],
      retainContextWhenHidden: true,
    }
  );

  panel.webview.html = renderHtml(panel.webview, distDir, indexPath);

  panel.onDidDispose(() => {
    panel = undefined;
  });
}

function renderHtml(webview: vscode.Webview, distDir: string, indexPath: string): string {
  let html = fs.readFileSync(indexPath, "utf-8");

  // Vite emits root-relative asset paths ("/assets/index-XXXX.js"). Rewrite
  // each src="/..."/href="/..." to a webview-resource URI pointing at the
  // real file under distDir — this is the standard pattern for hosting a
  // built SPA inside a VS Code webview.
  html = html.replace(/(src|href)="\/([^"]+)"/g, (_match, attr: string, rel: string) => {
    const onDisk = vscode.Uri.file(path.join(distDir, rel));
    const webviewUri = webview.asWebviewUri(onDisk);
    return `${attr}="${webviewUri.toString()}"`;
  });

  // Webviews have their own CSP; the default blocks the app's own fetch/WS
  // calls to the local backend, AND the app's Google Fonts links (checked
  // against the real built index.html before writing this — it loads
  // Newsreader/Space Grotesk/JetBrains Mono from fonts.googleapis.com /
  // fonts.gstatic.com). Allow all of it explicitly, not just the webview's
  // own resource origin.
  const csp = [
    `default-src 'none'`,
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline' https://fonts.googleapis.com`,
    `script-src ${webview.cspSource}`,
    `font-src ${webview.cspSource} https://fonts.gstatic.com`,
    `connect-src http://localhost:8000 ws://localhost:8000`,
  ].join("; ");
  const cspTag = `<meta http-equiv="Content-Security-Policy" content="${csp}">`;

  if (html.includes("<head>")) {
    html = html.replace("<head>", `<head>\n${cspTag}`);
  } else {
    html = cspTag + html;
  }

  return html;
}
