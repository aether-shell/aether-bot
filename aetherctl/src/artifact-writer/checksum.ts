import { createHash } from "node:crypto";
import fs from "node:fs";

/** Compute SHA256 hash of a file. */
export function computeSha256(filePath: string): string {
  const content = fs.readFileSync(filePath);
  return createHash("sha256").update(content).digest("hex");
}

/** Compute SHA256 hash of a string/buffer. */
export function computeSha256FromContent(content: string | Buffer): string {
  return createHash("sha256").update(content).digest("hex");
}
