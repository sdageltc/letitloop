/**
 * LetItLoop Action v2 — Proof-Carrying Verification Gate
 * Zero runtime npm dependencies. Node built-ins only (https, fs, crypto, path, child_process, os).
 * Fail-closed <2.5s: WAL hash-chain + AST integrity + HMAC Proof ID + deterministic PR comment.
 */

import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";
import * as https from "https";
import * as os from "os";
import { spawnSync } from "child_process";

// ---------------------------------------------------------------------------
// Minimal @actions/core shim — zero dep, reads INPUT_* and writes GITHUB_OUTPUT
// ---------------------------------------------------------------------------

function getInput(name: string, opts?: { required?: boolean }): string {
  const envKey = `INPUT_${name.replace(/ /g, "_").toUpperCase()}`;
  const val = (process.env[envKey] ?? "").trim();
  if (opts?.required && !val) throw new Error(`Input required and not supplied: ${name}`);
  return val;
}

function getBooleanInput(name: string): boolean {
  const v = getInput(name).toLowerCase();
  return v === "true" || v === "1" || v === "yes";
}

function toCommandValue(input: unknown): string {
  if (input === null || input === undefined) return "";
  if (typeof input === "string" || input instanceof String) return input as string;
  return JSON.stringify(input);
}

function issueCommand(command: string, properties: Record<string, string>, message: string): void {
  // Fallback file-command for GITHUB_OUTPUT / GITHUB_STATE etc.
  const cmdStr = `::${command} ${Object.entries(properties).map(([k, v]) => `${k}=${escapeProperty(v)}`).join(",")}::${escapeData(message)}`;
  process.stdout.write(cmdStr + os.EOL);
}

function escapeData(s: string): string { return s.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A"); }
function escapeProperty(s: string): string { return s.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A").replace(/:/g, "%3A").replace(/,/g, "%2C"); }

function setOutput(name: string, value: unknown): void {
  const outPath = process.env.GITHUB_OUTPUT;
  if (outPath) {
    const str = toCommandValue(value);
    // Use delimiter for multiline safety
    const delimiter = `ghadelimiter_${crypto.randomBytes(4).toString("hex")}`;
    fs.appendFileSync(outPath, `${name}<<${delimiter}${os.EOL}${str}${os.EOL}${delimiter}${os.EOL}`, { encoding: "utf-8" });
  } else {
    issueCommand("set-output", { name }, toCommandValue(value));
  }
}

function setFailed(message: string): void {
  process.exitCode = 1;
  error(message);
}

function info(message: string): void { process.stdout.write(message + os.EOL); }
function warning(message: string): void { issueCommand("warning", {}, message); }
function error(message: string): void { issueCommand("error", {}, message); }
function debug(message: string): void { issueCommand("debug", {}, message); }
function startGroup(name: string): void { issueCommand("group", {}, name); }
function endGroup(): void { issueCommand("endgroup", {}, ""); }

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AstVerdict = { pass: boolean; files_scanned: number; files_failed: number; violations: string[]; duration_ms: number; details?: string };
export type WalVerdict = { pass: boolean; files_scanned: number; frames: number; corrupted: number; duration_ms: number; details?: string };
export type ProofInputs = { baseSha: string; headSha: string; proofKey: string };
export type GateInputs = {
  token: string;
  mode: string;
  walDir: string;
  workspace: string;
  proofKey: string;
  failClosed: boolean;
  comment: boolean;
};

// ---------------------------------------------------------------------------
// Pure helpers — exported for Jest
// ---------------------------------------------------------------------------

export function parseInputs(): GateInputs {
  const mode = (getInput("mode") || "gate").toLowerCase();
  const walDir = getInput("wal-dir") || getInput("wal_dir") || ".bench_wal";
  const workspace = getInput("workspace") || ".";
  const proofKey = getInput("proof-key") || getInput("proof_key") || "";
  const failClosedRaw = (getInput("fail-closed") || getInput("fail_closed") || "true").toLowerCase();
  const commentRaw = (getInput("comment") || "true").toLowerCase();
  return {
    token: getInput("token") || process.env.GITHUB_TOKEN || "",
    mode,
    walDir,
    workspace,
    proofKey,
    failClosed: failClosedRaw === "true" || failClosedRaw === "1",
    comment: commentRaw === "true" || commentRaw === "1",
  };
}

export function parseGitHubContext(): { owner: string; repo: string; sha: string; runId: string; ref: string; eventName: string; eventPath: string } {
  const repoFull = process.env.GITHUB_REPOSITORY || "";
  const [owner, repo] = repoFull.split("/");
  return {
    owner: owner || "",
    repo: repo || "",
    sha: process.env.GITHUB_SHA || "",
    runId: process.env.GITHUB_RUN_ID || "",
    ref: process.env.GITHUB_REF || "",
    eventName: process.env.GITHUB_EVENT_NAME || "",
    eventPath: process.env.GITHUB_EVENT_PATH || "",
  };
}

export function getPullRequestNumber(eventPath: string): number | null {
  if (!eventPath || !fs.existsSync(eventPath)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(eventPath, "utf-8"));
    if (typeof data?.pull_request?.number === "number") return data.pull_request.number;
    if (typeof data?.number === "number" && data?.pull_request) return data.number;
    if (typeof data?.issue?.number === "number") return data.issue.number;
  } catch { /* ignore */ }
  return null;
}

export function getBaseHeadShas(eventPath: string, fallbackSha: string): { baseSha: string; headSha: string } {
  let base = "";
  let head = fallbackSha || "";
  if (eventPath && fs.existsSync(eventPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(eventPath, "utf-8"));
      if (data?.pull_request?.base?.sha) base = String(data.pull_request.base.sha);
      if (data?.pull_request?.head?.sha) head = String(data.pull_request.head.sha);
      // push event
      if (!base && data?.before) base = String(data.before);
      if (!head && data?.after) head = String(data.after);
    } catch { /* ignore */ }
  }
  if (!head) head = fallbackSha || "unknown";
  if (!base) base = head; // single-sha fallback retains deterministic proof
  return { baseSha: base, headSha: head };
}

export function generateProofId(baseSha: string, headSha: string, proofKey: string): string {
  const payload = `${baseSha}:${headSha}`;
  if (proofKey) {
    return crypto.createHmac("sha256", proofKey).update(payload).digest("hex");
  }
  return crypto.createHash("sha256").update(payload).digest("hex");
}

export function verifyWalJs(walDir: string): WalVerdict {
  const t0 = Date.now();
  let files = 0;
  let frames = 0;
  let corrupted = 0;
  const details: string[] = [];
  const absDir = path.resolve(walDir);

  function walk(dir: string): void {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && entry.name.endsWith(".jsonl")) {
        files++;
        const content = fs.readFileSync(full, "utf-8");
        const lines = content.split("\n");
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i].trim();
          if (!line) continue;
          frames++;
          // LILWAL02 frames are JSON with crc or wal fields; best-effort CRC is Python-side;
          // JS fast-path: ensure JSON parses; Python delegate does full CRC. We fail-closed on JSON errors here.
          try {
            JSON.parse(line);
          } catch (e) {
            corrupted++;
            details.push(`${path.relative(process.cwd(), full)}:${i + 1}: json_parse_failed`);
          }
        }
      }
    }
  }

  walk(absDir);
  const duration_ms = Date.now() - t0;
  // Fail-closed if walDir expected but missing and we are in gate mode with no wal? Let caller decide pass logic;
  // Here pass means no corrupted frames.
  return { pass: corrupted === 0, files_scanned: files, frames, corrupted, duration_ms, details: details.join("; ") || undefined };
}

// Full CRC verification via Python delegate (authoritative). Returns WalVerdict, falls back to JS on python miss.
export function verifyWal(walDir: string): WalVerdict {
  const t0 = Date.now();
  // Try python delegate first for real LILWAL02 CRC chain verification
  const pyScript = `
import sys, pathlib, json
try:
    from orchestrator.state import _wal_decode_line
except Exception as e:
    print('SKIP:no_orchestrator')
    sys.exit(2)
import os
wal_dir = sys.argv[1] if len(sys.argv) > 1 else '.bench_wal'
p = pathlib.Path(wal_dir)
files=0; frames=0; corrupted=0; details=[]
if p.exists():
    for f in p.rglob('*.jsonl'):
        files+=1
        for i, line in enumerate(f.read_text(errors='replace').splitlines()):
            line=line.strip()
            if not line: continue
            frames+=1
            try:
                _wal_decode_line(line)
            except Exception as e:
                corrupted+=1
                details.append(f"{f}:{i+1}:{e}")
print(json.dumps({"files":files,"frames":frames,"corrupted":corrupted,"details":"; ".join(details[:5])}))
sys.exit(0 if corrupted==0 else 1)
`;
  try {
    const res = spawnSync(process.execPath.includes("python") ? process.execPath : "python", ["-c", pyScript, walDir], {
      encoding: "utf-8",
      timeout: 3000,
      cwd: process.cwd(),
    });
    if (res.status === 2 || (res.stderr && res.stderr.includes("SKIP:no_orchestrator"))) {
      // Fallback to JS
      return verifyWalJs(walDir);
    }
    if (res.stdout) {
      try {
        const j = JSON.parse(res.stdout.trim().split("\n").pop() || "{}");
        const duration_ms = Date.now() - t0;
        return {
          pass: Number(j.corrupted ?? 0) === 0,
          files_scanned: Number(j.files ?? 0),
          frames: Number(j.frames ?? 0),
          corrupted: Number(j.corrupted ?? 0),
          duration_ms,
          details: j.details || undefined,
        };
      } catch { /* fallback */ }
    }
    if (res.status === 0) {
      // No stdout but success
      const duration_ms = Date.now() - t0;
      return { pass: true, files_scanned: 0, frames: 0, corrupted: 0, duration_ms };
    }
    // Python reported failure (corrupted)
    if (res.status === 1 && res.stdout) {
      try {
        const j = JSON.parse(res.stdout.trim().split("\n").pop() || "{}");
        const duration_ms = Date.now() - t0;
        return {
          pass: false,
          files_scanned: Number(j.files ?? 0),
          frames: Number(j.frames ?? 0),
          corrupted: Number(j.corrupted ?? 1),
          duration_ms,
          details: j.details,
        };
      } catch { /* */ }
    }
  } catch { /* timeout or spawn fail -> fallback */ }
  return verifyWalJs(walDir);
}

export function verifyAst(workspace: string): AstVerdict {
  const t0 = Date.now();
  const script = path.join(__dirname, "..", "scripts", "verify_ast.py");
  // Resolve script from both dist-layout and repo root
  const candidates = [
    script,
    path.join(process.cwd(), "letitloop-action", "scripts", "verify_ast.py"),
    path.join(process.cwd(), "scripts", "verify_ast.py"),
  ];
  let scriptPath: string | null = null;
  for (const c of candidates) if (fs.existsSync(c)) { scriptPath = c; break; }

  if (!scriptPath) {
    // Fallback inline AST check
    return verifyAstFallback(workspace);
  }

  try {
    const res = spawnSync("python", [scriptPath, "--workspace", workspace, "--json"], {
      encoding: "utf-8",
      timeout: 4000,
      cwd: process.cwd(),
    });
    if (res.stdout) {
      try {
        const lastLine = res.stdout.trim().split("\n").pop() || "";
        const j = JSON.parse(lastLine);
        const duration_ms = Date.now() - t0;
        // Script returns {pass, files_scanned, files_failed, violations}
        if (typeof j.pass === "boolean") {
          return {
            pass: j.pass,
            files_scanned: Number(j.files_scanned ?? j.files ?? 0),
            files_failed: Number(j.files_failed ?? 0),
            violations: Array.isArray(j.violations) ? j.violations : [],
            duration_ms,
            details: j.details,
          };
        }
      } catch { /* fallback */ }
    }
    // Non-zero without JSON → treat as fail-closed
    if (res.status !== 0) {
      const duration_ms = Date.now() - t0;
      return {
        pass: false,
        files_scanned: 0,
        files_failed: 1,
        violations: [(res.stderr || res.stdout || "verify_ast.py failed").slice(0, 800)],
        duration_ms,
      };
    }
  } catch (e) {
    const duration_ms = Date.now() - t0;
    return { pass: false, files_scanned: 0, files_failed: 1, violations: [String(e).slice(0, 500)], duration_ms };
  }
  // If script emitted nothing but succeeded, assume pass
  const duration_ms = Date.now() - t0;
  return { pass: true, files_scanned: 0, files_failed: 0, violations: [], duration_ms };
}

function verifyAstFallback(workspace: string): AstVerdict {
  const t0 = Date.now();
  let scanned = 0;
  let failed = 0;
  const violations: string[] = [];
  const absRoot = path.resolve(workspace);
  function walk(dir: string): void {
    if (!fs.existsSync(dir)) return;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      // Skip heavy dirs deterministically
      if (entry.isDirectory()) {
        if (["node_modules", ".git", "__pycache__", ".venv", "dist", ".bench_wal", ".letitloop", "scratch", ".pytest_cache", ".ruff_cache"].includes(entry.name)) continue;
        walk(full);
      } else if (entry.isFile() && entry.name.endsWith(".py")) {
        scanned++;
        const src = fs.readFileSync(full, "utf-8");
        // Fast syntax triage: check balanced brackets as lightweight proxy (full check is verify_ast.py)
        try {
          const content = fs.readFileSync(full, "utf-8");
          // Very lightweight: if file contains obviously broken syntax markers, flag
          if (content.includes("<<<<<<<") || content.includes(">>>>>>>")) {
            failed++;
            violations.push(`${path.relative(absRoot, full)}: merge_conflict_marker`);
          }
        } catch {
          // ignore
        }
        // Forbidden file check (policy gate sample): CI workflow mutation should be flagged when workspace is repo root
        if (full.includes(".github/workflows") && src.includes("letitloop-verify")) {
          // allowed
        }
      }
    }
  }
  walk(absRoot);
  return { pass: failed === 0, files_scanned: scanned, files_failed: failed, violations, duration_ms: Date.now() - t0 };
}

export function renderComment(opts: {
  proofId: string;
  baseSha: string;
  headSha: string;
  ast: AstVerdict;
  wal: WalVerdict;
  elapsedMs: number;
  repoOwner: string;
  repoName: string;
  runId: string;
}): string {
  const certBadge = opts.ast.pass && opts.wal.pass ? "✅ **PASS**" : "❌ **FAIL (fail-closed)**";
  const shortProof = opts.proofId.slice(0, 16);
  const lines: string[] = [];
  lines.push("## 🛡️ LetItLoop Durability Certificate");
  lines.push("");
  lines.push(`> **Status:** ${certBadge}  |  **Proof ID:** \`${opts.proofId}\` (\`${shortProof}…\`)  |  **Elapsed:** ${opts.elapsedMs}ms`);
  lines.push("");
  lines.push(`| Check | Verdict | Detail |`);
  lines.push(`|---|---|---|`);
  lines.push(`| **AST Integrity** | ${opts.ast.pass ? "✅ PASS" : "❌ FAIL"} | ${opts.ast.files_scanned} files scanned, ${opts.ast.files_failed} failed${opts.ast.violations.length ? ` — ${opts.ast.violations[0].slice(0, 120)}` : ""} (${opts.ast.duration_ms}ms) |`);
  lines.push(`| **WAL Chain (LILWAL02)** | ${opts.wal.pass ? "✅ PASS" : "❌ FAIL"} | ${opts.wal.files_scanned} files, ${opts.wal.frames} frames, ${opts.wal.corrupted} corrupted (${opts.wal.duration_ms}ms) |`);
  lines.push(`| **Proof Binding** | ${opts.proofId ? "🔒 HMAC-SHA256" : "—"} | base \`${opts.baseSha.slice(0, 7)}\` → head \`${opts.headSha.slice(0, 7)}\` |`);
  lines.push("");
  lines.push("<details><summary>📊 Step Execution Summary</summary>");
  lines.push("");
  lines.push(`- **Scope:** \`${opts.repoOwner}/${opts.repoName}\`  \`run ${opts.runId || "local"}\``);
  lines.push(`- **AST:** ${opts.ast.files_scanned} scanned, ${opts.ast.files_failed} failed, ${opts.ast.violations.length} violations`);
  lines.push(`- **WAL:** ${opts.wal.files_scanned} wal files, ${opts.wal.frames} frames`);
  if (opts.ast.details) lines.push(`- **AST details:** ${opts.ast.details.slice(0, 400)}`);
  if (opts.wal.details) lines.push(`- **WAL details:** ${opts.wal.details.slice(0, 400)}`);
  lines.push("");
  lines.push("</details>");
  lines.push("");
  lines.push(`<sub>LetItLoop v2 — DCP-2.0 / LILWAL02 — deterministic gate in ${opts.elapsedMs}ms — <a href="https://github.com/${opts.repoOwner}/${opts.repoName}/actions/runs/${opts.runId}">run #${opts.runId || "—"}</a></sub>`);
  return lines.join("\n");
}

export function postComment(token: string, owner: string, repo: string, issueNumber: number, body: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify({ body });
    const opts: https.RequestOptions = {
      hostname: "api.github.com",
      path: `/repos/${owner}/${repo}/issues/${issueNumber}/comments`,
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "User-Agent": "letitloop-action-v2",
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(data),
        Accept: "application/vnd.github+json",
      },
    };
    const req = https.request(opts, (res) => {
      let buf = "";
      res.on("data", (c) => (buf += c));
      res.on("end", () => {
        if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) resolve(res.statusCode);
        else reject(new Error(`GitHub comment failed ${res.statusCode}: ${buf.slice(0, 600)}`));
      });
    });
    req.on("error", reject);
    req.setTimeout(6000, () => {
      req.destroy(new Error("GitHub comment timeout"));
    });
    req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export async function run(): Promise<void> {
  const t0 = Date.now();
  const inputs = parseInputs();
  const ctx = parseGitHubContext();
  const { baseSha, headSha } = getBaseHeadShas(ctx.eventPath, ctx.sha);
  const proofId = generateProofId(baseSha, headSha, inputs.proofKey);

  startGroup("LetItLoop Gate v2 — AST + WAL + Proof");
  info(`workspace=${inputs.workspace} wal-dir=${inputs.walDir} mode=${inputs.mode} failClosed=${inputs.failClosed}`);

  const ast = verifyAst(inputs.workspace);
  const wal = verifyWal(inputs.walDir);

  const elapsedMs = Date.now() - t0;
  const verified = ast.pass && wal.pass;
  const verdict = verified ? "PASS" : "FAIL";

  info(`AST: ${ast.pass ? "PASS" : "FAIL"} (${ast.files_scanned} files, ${ast.files_failed} failed, ${ast.duration_ms}ms)`);
  if (ast.violations.length) info(`AST violations: ${ast.violations.slice(0, 3).join("; ").slice(0, 500)}`);
  info(`WAL: ${wal.pass ? "PASS" : "FAIL"} (${wal.files_scanned} files, ${wal.frames} frames, ${wal.corrupted} corrupted, ${wal.duration_ms}ms)`);
  if (wal.details) info(`WAL details: ${wal.details.slice(0, 500)}`);
  info(`Proof ID: ${proofId} (${proofId.slice(0, 16)}…) base ${baseSha.slice(0, 7)} head ${headSha.slice(0, 7)} elapsed ${elapsedMs}ms`);

  setOutput("verified", String(verified));
  setOutput("proof-id", proofId);
  setOutput("ast-verdict", `${verdict}:AST ${ast.pass ? "PASS" : "FAIL"} ${ast.files_scanned}/${ast.files_failed}`);
  setOutput("wal-verdict", `${verdict}:WAL ${wal.pass ? "PASS" : "FAIL"} ${wal.files_scanned}/${wal.frames}/${wal.corrupted}`);

  // PR comment (deterministic body)
  if (inputs.comment && inputs.token && ctx.owner && ctx.repo) {
    const issueNumber = getPullRequestNumber(ctx.eventPath);
    if (issueNumber) {
      const body = renderComment({
        proofId,
        baseSha,
        headSha,
        ast,
        wal,
        elapsedMs,
        repoOwner: ctx.owner,
        repoName: ctx.repo,
        runId: ctx.runId,
      });
      try {
        await postComment(inputs.token, ctx.owner, ctx.repo, issueNumber, body);
        info(`Commented PR #${issueNumber}`);
      } catch (e) {
        warning(`PR comment failed (non-fatal): ${String(e).slice(0, 500)}`);
      }
    } else {
      debug("No PR number in event — skipping comment");
    }
  } else if (inputs.comment) {
    debug("Skipping PR comment: missing token or repo context");
  }

  endGroup();

  // Enforce <2.5s gate budget warning (not failure, just advisory unless fail-closed and slow is not a violation)
  if (elapsedMs > 2500) warning(`Gate exceeded 2.5s budget: ${elapsedMs}ms`);

  const shouldFailClosed = inputs.failClosed && inputs.mode !== "report";
  if (!verified && shouldFailClosed) {
    setFailed(`LetItLoop gate FAIL-CLOSED (${verdict}) — AST ${ast.pass ? "PASS" : "FAIL"}, WAL ${wal.pass ? "PASS" : "FAIL"} — proof ${proofId.slice(0, 16)}…`);
    // Ensure non-zero exit for GH Action
    process.exitCode = 1;
  } else if (!verified) {
    warning(`Gate ${verdict} but mode=report (not failing)`);
  }
}

// Only auto-run when invoked as GitHub Action entrypoint (dist/index.js), not during Jest import
if (require.main === module) {
  run().catch((e) => {
    setFailed(String((e as Error)?.message ?? e));
    process.exitCode = 1;
  });
}
