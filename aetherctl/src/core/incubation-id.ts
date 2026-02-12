import fs from "node:fs";
import path from "node:path";

/**
 * Generate an incubation ID.
 * Format: {branch}-{base_sha_7}-{YYYYMMDD}-{seq}
 */
export function generateIncubationId(
  branch: string,
  baseSha: string,
  artifactsDir: string,
): string {
  const safeBranch = branch.replace(/[^a-zA-Z0-9_-]/g, "-").slice(0, 30);
  const sha7 = baseSha.slice(0, 7);
  const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const seq = getNextSeq(artifactsDir, `${safeBranch}-${sha7}-${date}`);
  return `${safeBranch}-${sha7}-${date}-${seq}`;
}

function getNextSeq(artifactsDir: string, prefix: string): string {
  if (!fs.existsSync(artifactsDir)) return "001";

  const entries = fs.readdirSync(artifactsDir, { withFileTypes: true });
  let maxSeq = 0;

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    if (!entry.name.startsWith(prefix)) continue;
    const parts = entry.name.split("-");
    const last = parts[parts.length - 1];
    const num = parseInt(last, 10);
    if (!isNaN(num) && num > maxSeq) maxSeq = num;
  }

  return String(maxSeq + 1).padStart(3, "0");
}
