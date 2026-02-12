import fs from "node:fs";
import path from "node:path";

export type ArtifactsResult =
  | { ok: true; files: Array<{ path: string; size: number }> }
  | { ok: false; error: string };

/**
 * List artifacts for a given incubation.
 */
export function listArtifacts(opts: { artifactsDir: string; incubationId: string }): ArtifactsResult {
  const dir = path.join(opts.artifactsDir, opts.incubationId);

  if (!fs.existsSync(dir)) {
    return { ok: false, error: `No artifacts found for: ${opts.incubationId}` };
  }

  const files: Array<{ path: string; size: number }> = [];
  collectFiles(dir, dir, files);

  return { ok: true, files };
}

function collectFiles(baseDir: string, currentDir: string, out: Array<{ path: string; size: number }>): void {
  const entries = fs.readdirSync(currentDir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(currentDir, entry.name);
    if (entry.isDirectory()) {
      collectFiles(baseDir, fullPath, out);
    } else if (entry.isFile()) {
      const stat = fs.statSync(fullPath);
      out.push({ path: path.relative(baseDir, fullPath), size: stat.size });
    }
  }
}
