/**
 * Jest suite for letitloop-action v2 — mocks GitHub Actions runner context.
 * Must pass 100% for Sprint 3 exit gate.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as crypto from "crypto";

// Import pure helpers (no side-effects on import because src checks require.main)
import {
  generateProofId,
  getBaseHeadShas,
  getPullRequestNumber,
  parseInputs,
  renderComment,
  verifyWalJs,
} from "../src/index";

// Helpers
function withEnv<T>(env: Record<string, string | undefined>, fn: () => T): T {
  const prev: Record<string, string | undefined> = {};
  for (const k of Object.keys(env)) {
    prev[k] = process.env[k];
    if (env[k] === undefined) delete process.env[k];
    else process.env[k] = env[k] as string;
  }
  try {
    return fn();
  } finally {
    for (const k of Object.keys(prev)) {
      if (prev[k] === undefined) delete process.env[k];
      else process.env[k] = prev[k] as string;
    }
    // Clean any INPUT_ leftovers that weren't in prev
    for (const k of Object.keys(env)) if (!(k in prev)) delete process.env[k];
  }
}

describe("parseInputs", () => {
  it("defaults mode=gate, walDir=.bench_wal, workspace=., failClosed=true", () => {
    const got = withEnv(
      {
        INPUT_MODE: undefined,
        INPUT_WAL_DIR: undefined,
        INPUT_WORKSPACE: undefined,
        INPUT_FAIL_CLOSED: undefined,
        INPUT_COMMENT: undefined,
        INPUT_PROOF_KEY: undefined,
        INPUT_TOKEN: undefined,
      },
      () => parseInputs()
    );
    expect(got.mode).toBe("gate");
    expect(got.walDir).toBe(".bench_wal");
    expect(got.workspace).toBe(".");
    expect(got.failClosed).toBe(true);
    expect(got.comment).toBe(true);
  });

  it("parses explicit inputs", () => {
    const got = withEnv(
      {
        INPUT_MODE: "report",
        INPUT_WAL_DIR: "my_wal",
        INPUT_WORKSPACE: "my_ws",
        INPUT_FAIL_CLOSED: "false",
        INPUT_COMMENT: "false",
        INPUT_PROOF_KEY: "secret123",
        INPUT_TOKEN: "ghp_test",
      },
      () => parseInputs()
    );
    expect(got.mode).toBe("report");
    expect(got.walDir).toBe("my_wal");
    expect(got.workspace).toBe("my_ws");
    expect(got.failClosed).toBe(false);
    expect(got.comment).toBe(false);
    expect(got.proofKey).toBe("secret123");
    expect(got.token).toBe("ghp_test");
  });
});

describe("generateProofId", () => {
  it("is deterministic SHA256 when no proofKey", () => {
    const a = generateProofId("abc123", "def456", "");
    const b = generateProofId("abc123", "def456", "");
    expect(a).toBe(b);
    expect(a).toHaveLength(64);
    expect(a).toBe(crypto.createHash("sha256").update("abc123:def456").digest("hex"));
  });

  it("is HMAC-SHA256 when proofKey provided", () => {
    const id = generateProofId("base", "head", "mykey");
    const expected = crypto.createHmac("sha256", "mykey").update("base:head").digest("hex");
    expect(id).toBe(expected);
  });

  it("different base/head produce different proofIds", () => {
    const a = generateProofId("a", "b", "");
    const b = generateProofId("a", "c", "");
    expect(a).not.toBe(b);
  });
});

describe("getPullRequestNumber", () => {
  it("returns PR number from pull_request event", () => {
    const tmp = path.join(os.tmpdir(), `letitloop-test-event-${Date.now()}.json`);
    fs.writeFileSync(tmp, JSON.stringify({ pull_request: { number: 42, base: { sha: "aaa" }, head: { sha: "bbb" } } }), "utf-8");
    expect(getPullRequestNumber(tmp)).toBe(42);
    fs.unlinkSync(tmp);
  });

  it("returns null on missing file", () => {
    expect(getPullRequestNumber("/no/such/file.json")).toBeNull();
  });

  it("returns null on malformed JSON", () => {
    const tmp = path.join(os.tmpdir(), `letitloop-bad-${Date.now()}.json`);
    fs.writeFileSync(tmp, "{ not json", "utf-8");
    expect(getPullRequestNumber(tmp)).toBeNull();
    fs.unlinkSync(tmp);
  });
});

describe("getBaseHeadShas", () => {
  it("extracts base/head from PR event", () => {
    const tmp = path.join(os.tmpdir(), `letitloop-shas-${Date.now()}.json`);
    fs.writeFileSync(tmp, JSON.stringify({ pull_request: { base: { sha: "BASE123" }, head: { sha: "HEAD456" } } }), "utf-8");
    const { baseSha, headSha } = getBaseHeadShas(tmp, "fallback");
    expect(baseSha).toBe("BASE123");
    expect(headSha).toBe("HEAD456");
    fs.unlinkSync(tmp);
  });

  it("falls back to GITHUB_SHA when no event", () => {
    const { baseSha, headSha } = getBaseHeadShas("/no/file", "FALLBACKSHA");
    expect(headSha).toBe("FALLBACKSHA");
    expect(baseSha).toBe("FALLBACKSHA");
  });
});

describe("verifyWalJs", () => {
  it("passes on empty/missing wal dir (0 corrupted)", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wal-empty-"));
    const v = verifyWalJs(tmp);
    expect(v.pass).toBe(true);
    expect(v.corrupted).toBe(0);
    fs.rmdirSync(tmp);
  });

  it("passes on valid jsonl frames", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wal-valid-"));
    fs.writeFileSync(path.join(tmp, "a.jsonl"), JSON.stringify({ seq: 1, data: "x" }) + "\n" + JSON.stringify({ seq: 2 }) + "\n", "utf-8");
    const v = verifyWalJs(tmp);
    expect(v.pass).toBe(true);
    expect(v.frames).toBe(2);
    expect(v.files_scanned).toBe(1);
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it("fail-closed on corrupted json line", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "wal-bad-"));
    fs.writeFileSync(path.join(tmp, "bad.jsonl"), '{"ok":1}\n{ not json\n', "utf-8");
    const v = verifyWalJs(tmp);
    expect(v.pass).toBe(false);
    expect(v.corrupted).toBe(1);
    fs.rmSync(tmp, { recursive: true, force: true });
  });
});

describe("renderComment", () => {
  it("renders deterministic markdown with PASS/FAIL and proofId", () => {
    const ast = { pass: true, files_scanned: 12, files_failed: 0, violations: [], duration_ms: 42 } as const;
    const wal = { pass: true, files_scanned: 2, frames: 20, corrupted: 0, duration_ms: 11 } as const;
    const body = renderComment({
      proofId: "a".repeat(64),
      baseSha: "base1234567890",
      headSha: "head1234567890",
      ast: ast as any,
      wal: wal as any,
      elapsedMs: 123,
      repoOwner: "sdageltc",
      repoName: "letitloop",
      runId: "999",
    });
    expect(body).toContain("LetItLoop Durability Certificate");
    expect(body).toContain("PASS");
    expect(body).toContain("a".repeat(16));
    expect(body).toContain("AST Integrity");
    expect(body).toContain("WAL Chain");
    expect(body).toContain("base123");
    expect(body).toContain("sdageltc/letitloop");
  });

  it("renders FAIL badge when gate fails", () => {
    const ast = { pass: false, files_scanned: 1, files_failed: 1, violations: ["x.py:1 SyntaxError"], duration_ms: 5 } as const;
    const wal = { pass: false, files_scanned: 1, frames: 1, corrupted: 1, duration_ms: 5, details: "bad" } as const;
    const body = renderComment({
      proofId: "b".repeat(64),
      baseSha: "aaa",
      headSha: "bbb",
      ast: ast as any,
      wal: wal as any,
      elapsedMs: 50,
      repoOwner: "o",
      repoName: "r",
      runId: "",
    });
    expect(body).toContain("FAIL (fail-closed)");
    expect(body).toContain("AST Integrity");
  });

  it("is byte-deterministic for same inputs", () => {
    const ast = { pass: true, files_scanned: 5, files_failed: 0, violations: [], duration_ms: 1 } as const;
    const wal = { pass: true, files_scanned: 1, frames: 5, corrupted: 0, duration_ms: 1 } as const;
    const opts = {
      proofId: "c".repeat(64),
      baseSha: "AAA",
      headSha: "BBB",
      ast: ast as any,
      wal: wal as any,
      elapsedMs: 100,
      repoOwner: "o",
      repoName: "r",
      runId: "1",
    };
    expect(renderComment(opts)).toBe(renderComment(opts));
  });
});
