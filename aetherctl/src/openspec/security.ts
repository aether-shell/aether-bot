import { resolve, isAbsolute } from "node:path";

// Security constants
export const ALLOWED_COMMANDS = [
  "npm",
  "pnpm",
  "yarn",
  "git",
  "make",
  "python",
  "python3",
  "pytest",
  "node",
  "tsc",
  "eslint",
  "cargo",
  "go",
  "mvn",
  "gradle",
];

export const MAX_TASKS_FILE_SIZE = 1024 * 1024; // 1MB
export const MAX_LINE_LENGTH = 10000;
export const MAX_TASK_COUNT = 1000;
export const MAX_CMD_BUFFER_SIZE = 50 * 1024 * 1024; // 50MB
export const STALE_LOCK_AGE_MS = 300000; // 5 minutes

/**
 * Sanitize path component to prevent path traversal attacks.
 * @throws Error if path component is invalid
 */
export function sanitizePathComponent(component: string): string {
  if (!component || component.trim().length === 0) {
    throw new Error("Path component cannot be empty");
  }

  // Reject path traversal attempts
  if (
    component.includes("..") ||
    component.includes("/") ||
    component.includes("\\") ||
    component.includes("\0")
  ) {
    throw new Error(`Invalid path component: ${component}`);
  }

  return component.trim();
}

/**
 * Safely construct a path, ensuring it stays within the base directory.
 * @throws Error if path traversal is detected
 */
export function safePath(base: string, ...components: string[]): string {
  if (!isAbsolute(base)) {
    throw new Error(`Base path must be absolute: ${base}`);
  }

  // Sanitize all components
  const sanitized = components.map(sanitizePathComponent);

  // Construct and verify final path
  const fullPath = resolve(base, ...sanitized);
  const normalizedBase = resolve(base);

  if (!fullPath.startsWith(normalizedBase + "/") && fullPath !== normalizedBase) {
    throw new Error(`Path traversal detected: ${fullPath}`);
  }

  return fullPath;
}

/**
 * Validate command against whitelist and extract command + args safely.
 * @throws Error if command is not allowed
 */
export function validateCommand(cmd: string): { command: string; args: string[] } {
  if (!cmd || cmd.trim().length === 0) {
    throw new Error("Command cannot be empty");
  }

  const trimmed = cmd.trim();

  // Check for shell injection patterns
  const dangerousPatterns = [
    /[;&|`$()]/g, // Shell metacharacters
    /\n|\r/g, // Newlines
    />\s*\/dev/g, // Device access
    /rm\s+-rf\s+\//g, // Destructive commands
  ];

  for (const pattern of dangerousPatterns) {
    if (pattern.test(trimmed)) {
      throw new Error(`Command contains dangerous pattern: ${trimmed}`);
    }
  }

  // Parse command and args
  const parts = trimmed.split(/\s+/).filter((p) => p.length > 0);
  if (parts.length === 0) {
    throw new Error("Command cannot be empty after parsing");
  }

  const command = parts[0];
  const args = parts.slice(1);

  // Check whitelist
  if (!ALLOWED_COMMANDS.includes(command)) {
    throw new Error(
      `Command not in whitelist: ${command}. Allowed: ${ALLOWED_COMMANDS.join(", ")}`
    );
  }

  return { command, args };
}

/**
 * Sanitize log message to prevent log injection.
 */
export function sanitizeLogMessage(s: string): string {
  if (!s) return "";
  return s.replace(/[\r\n]/g, "\\n").replace(/\t/g, "\\t").slice(0, 10000);
}

/**
 * Redact sensitive information from error messages.
 */
export function redactSensitiveInfo(s: string): string {
  if (!s) return "";

  let result = s;

  // Redact common sensitive patterns
  result = result.replace(/password[=:]\s*\S+/gi, "password=***");
  result = result.replace(/token[=:]\s*\S+/gi, "token=***");
  result = result.replace(/api[_-]?key[=:]\s*\S+/gi, "api_key=***");
  result = result.replace(/secret[=:]\s*\S+/gi, "secret=***");
  result = result.replace(/\/home\/[^/\s]+/g, "/home/***");
  result = result.replace(/\/Users\/[^/\s]+/g, "/Users/***");

  return result;
}

/**
 * Validate task markdown content.
 * @throws Error if content is invalid
 */
export function validateTasksContent(content: string): void {
  if (content.length > MAX_TASKS_FILE_SIZE) {
    throw new Error(`Tasks file too large: ${content.length} bytes (max: ${MAX_TASKS_FILE_SIZE})`);
  }

  const lines = content.split("\n");
  if (lines.length > MAX_TASK_COUNT) {
    throw new Error(`Too many lines: ${lines.length} (max: ${MAX_TASK_COUNT})`);
  }

  for (let i = 0; i < lines.length; i++) {
    if (lines[i].length > MAX_LINE_LENGTH) {
      throw new Error(`Line ${i + 1} too long: ${lines[i].length} chars (max: ${MAX_LINE_LENGTH})`);
    }
  }
}

/**
 * Sanitize environment variables for subprocess execution.
 */
export function sanitizeEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const safe: NodeJS.ProcessEnv = {
    PATH: env.PATH,
    HOME: env.HOME,
    USER: env.USER,
    LANG: env.LANG,
    LC_ALL: env.LC_ALL,
    TERM: env.TERM,
  };

  // Remove undefined values
  Object.keys(safe).forEach((key) => {
    if (safe[key] === undefined) {
      delete safe[key];
    }
  });

  return safe;
}
