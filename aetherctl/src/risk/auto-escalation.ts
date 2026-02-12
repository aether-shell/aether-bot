import { minimatch } from "minimatch";

/**
 * Auto-escalation — checks if any changed file matches an auto-escalation pattern.
 * If matched, risk is automatically escalated to "high".
 */
export function checkAutoEscalation(
  filesChanged: string[],
  escalationPatterns: string[],
): { escalated: boolean; matchedFiles: string[]; matchedPattern?: string } {
  const matchedFiles: string[] = [];
  let matchedPattern: string | undefined;

  for (const pattern of escalationPatterns) {
    for (const file of filesChanged) {
      if (minimatch(file, pattern)) {
        matchedFiles.push(file);
        if (!matchedPattern) matchedPattern = pattern;
      }
    }
  }

  return {
    escalated: matchedFiles.length > 0,
    matchedFiles: [...new Set(matchedFiles)],
    matchedPattern,
  };
}
