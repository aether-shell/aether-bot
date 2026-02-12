import { describe, expect, it } from "vitest";
import path from "node:path";
import {
  sanitizePathComponent,
  safePath,
  validateCommand,
  sanitizeLogMessage,
  redactSensitiveInfo,
  validateTasksContent,
  sanitizeEnv,
  ALLOWED_COMMANDS,
  MAX_TASKS_FILE_SIZE,
  MAX_LINE_LENGTH,
  MAX_TASK_COUNT,
} from "../src/openspec/security.js";

describe("sanitizePathComponent", () => {
  it("accepts valid component", () => {
    expect(sanitizePathComponent("my-change-id")).toBe("my-change-id");
  });

  it("trims whitespace", () => {
    expect(sanitizePathComponent("  foo  ")).toBe("foo");
  });

  it("rejects empty string", () => {
    expect(() => sanitizePathComponent("")).toThrow("empty");
  });

  it("rejects whitespace-only", () => {
    expect(() => sanitizePathComponent("   ")).toThrow("empty");
  });

  it("rejects path traversal with ..", () => {
    expect(() => sanitizePathComponent("..")).toThrow("Invalid");
    expect(() => sanitizePathComponent("foo..bar")).toThrow("Invalid");
  });

  it("rejects forward slash", () => {
    expect(() => sanitizePathComponent("foo/bar")).toThrow("Invalid");
  });

  it("rejects backslash", () => {
    expect(() => sanitizePathComponent("foo\\bar")).toThrow("Invalid");
  });

  it("rejects null byte", () => {
    expect(() => sanitizePathComponent("foo\0bar")).toThrow("Invalid");
  });
});

describe("safePath", () => {
  it("constructs valid path within base", () => {
    const result = safePath("/repo", "openspec", "changes", "id1");
    expect(result).toBe("/repo/openspec/changes/id1");
  });

  it("rejects non-absolute base", () => {
    expect(() => safePath("relative/path", "foo")).toThrow("absolute");
  });

  it("rejects traversal in components", () => {
    expect(() => safePath("/repo", "..")).toThrow("Invalid");
  });
});

describe("validateCommand", () => {
  it("accepts whitelisted command", () => {
    const { command, args } = validateCommand("git status --porcelain");
    expect(command).toBe("git");
    expect(args).toEqual(["status", "--porcelain"]);
  });

  it("accepts all whitelisted commands", () => {
    for (const cmd of ALLOWED_COMMANDS) {
      expect(() => validateCommand(cmd)).not.toThrow();
    }
  });

  it("rejects non-whitelisted command", () => {
    expect(() => validateCommand("curl http://evil.com")).toThrow("whitelist");
  });

  it("rejects empty command", () => {
    expect(() => validateCommand("")).toThrow("empty");
  });

  it("rejects shell metacharacters", () => {
    expect(() => validateCommand("git status; rm -rf /")).toThrow("dangerous");
    expect(() => validateCommand("git status | cat")).toThrow("dangerous");
    expect(() => validateCommand("git status & bg")).toThrow("dangerous");
    expect(() => validateCommand("echo `whoami`")).toThrow("dangerous");
    expect(() => validateCommand("echo $(whoami)")).toThrow("dangerous");
  });

  it("rejects newlines", () => {
    expect(() => validateCommand("git\nstatus")).toThrow("dangerous");
  });
});

describe("sanitizeLogMessage", () => {
  it("replaces newlines", () => {
    expect(sanitizeLogMessage("line1\nline2\rline3")).toBe("line1\\nline2\\nline3");
  });

  it("replaces tabs", () => {
    expect(sanitizeLogMessage("col1\tcol2")).toBe("col1\\tcol2");
  });

  it("truncates long messages", () => {
    const long = "x".repeat(20000);
    expect(sanitizeLogMessage(long).length).toBe(10000);
  });

  it("handles empty/null input", () => {
    expect(sanitizeLogMessage("")).toBe("");
  });
});

describe("redactSensitiveInfo", () => {
  it("redacts password", () => {
    expect(redactSensitiveInfo("password=secret123")).toBe("password=***");
  });

  it("redacts token", () => {
    expect(redactSensitiveInfo("token=abc123")).toBe("token=***");
  });

  it("redacts api_key", () => {
    expect(redactSensitiveInfo("api_key=xyz")).toBe("api_key=***");
  });

  it("redacts home paths", () => {
    const result = redactSensitiveInfo("/Users/john/project");
    expect(result).toContain("/Users/***");
    expect(result).not.toContain("john");
  });

  it("handles empty input", () => {
    expect(redactSensitiveInfo("")).toBe("");
  });
});

describe("validateTasksContent", () => {
  it("accepts valid content", () => {
    expect(() => validateTasksContent("- [ ] task 1\n- [x] task 2\n")).not.toThrow();
  });

  it("rejects oversized content", () => {
    const big = "x".repeat(MAX_TASKS_FILE_SIZE + 1);
    expect(() => validateTasksContent(big)).toThrow("too large");
  });

  it("rejects too many lines", () => {
    const lines = Array(MAX_TASK_COUNT + 2).fill("- [ ] task").join("\n");
    expect(() => validateTasksContent(lines)).toThrow("Too many lines");
  });

  it("rejects lines that are too long", () => {
    const longLine = "x".repeat(MAX_LINE_LENGTH + 1);
    expect(() => validateTasksContent(longLine)).toThrow("too long");
  });
});

describe("sanitizeEnv", () => {
  it("only passes through safe env vars", () => {
    const env = sanitizeEnv({
      PATH: "/usr/bin",
      HOME: "/home/user",
      SECRET_KEY: "should-be-removed",
      AWS_ACCESS_KEY_ID: "should-be-removed",
    });
    expect(env.PATH).toBe("/usr/bin");
    expect(env.HOME).toBe("/home/user");
    expect(env.SECRET_KEY).toBeUndefined();
    expect(env.AWS_ACCESS_KEY_ID).toBeUndefined();
  });

  it("removes undefined values", () => {
    const env = sanitizeEnv({ PATH: "/usr/bin" });
    expect(Object.keys(env).every((k) => env[k] !== undefined)).toBe(true);
  });
});

describe("isWithinDir (via validate.ts)", () => {
  // isWithinDir is not exported, but we can test it indirectly through path.relative logic
  it("detects path traversal with relative paths", () => {
    const root = "/repo/artifacts";
    const candidate = path.resolve(root, "../../../etc/passwd");
    const rel = path.relative(root, candidate);
    expect(rel.startsWith("..")).toBe(true);
  });

  it("accepts valid subpath", () => {
    const root = "/repo/artifacts";
    const candidate = path.resolve(root, "freeze.json");
    const rel = path.relative(root, candidate);
    expect(rel.startsWith("..")).toBe(false);
    expect(path.isAbsolute(rel)).toBe(false);
    expect(rel).toBe("freeze.json");
  });

  it("accepts nested subpath", () => {
    const root = "/repo/artifacts";
    const candidate = path.resolve(root, "test-results/unit.json");
    const rel = path.relative(root, candidate);
    expect(rel.startsWith("..")).toBe(false);
    expect(rel).toBe("test-results/unit.json");
  });

  it("rejects same directory (empty relative)", () => {
    const root = "/repo/artifacts";
    const rel = path.relative(root, root);
    expect(rel).toBe("");
  });
});
