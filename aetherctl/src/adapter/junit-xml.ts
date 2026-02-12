import { XMLParser } from "fast-xml-parser";
import fs from "node:fs";
import type { TestResult, TestFailure } from "../types/adapter-output.js";

/**
 * Parse JUnit XML and convert to normalized TestResult.
 */
export function parseJunitXml(xmlContent: string): TestResult {
  const parser = new XMLParser({
    ignoreAttributes: false,
    attributeNamePrefix: "@_",
    isArray: (name) => name === "testsuite" || name === "testcase" || name === "failure" || name === "error",
  });

  const parsed = parser.parse(xmlContent);

  // Handle both <testsuites> wrapper and single <testsuite>
  let suites: any[];
  if (parsed.testsuites?.testsuite) {
    suites = Array.isArray(parsed.testsuites.testsuite)
      ? parsed.testsuites.testsuite
      : [parsed.testsuites.testsuite];
  } else if (parsed.testsuite) {
    suites = Array.isArray(parsed.testsuite) ? parsed.testsuite : [parsed.testsuite];
  } else {
    // Empty or unrecognized format
    return { pass: true, total: 0, passed: 0, failed: 0, skipped: 0, duration_ms: 0, failures: [] };
  }

  let total = 0;
  let failed = 0;
  let skipped = 0;
  let durationSec = 0;
  const failures: TestFailure[] = [];

  for (const suite of suites) {
    const sTests = parseInt(suite["@_tests"] ?? "0", 10);
    const sFails = parseInt(suite["@_failures"] ?? "0", 10);
    const sErrors = parseInt(suite["@_errors"] ?? "0", 10);
    const sSkipped = parseInt(suite["@_skipped"] ?? "0", 10);
    const sTime = parseFloat(suite["@_time"] ?? "0");

    total += sTests;
    failed += sFails + sErrors;
    skipped += sSkipped;
    durationSec += sTime;

    // Extract failure details from test cases
    const cases = suite.testcase ?? [];
    for (const tc of Array.isArray(cases) ? cases : [cases]) {
      const tcName = tc["@_name"] ?? "unknown";
      const tcClass = tc["@_classname"] ?? "";
      const fullName = tcClass ? `${tcClass}.${tcName}` : tcName;

      // Check for <failure> elements
      if (tc.failure) {
        const failList = Array.isArray(tc.failure) ? tc.failure : [tc.failure];
        for (const f of failList) {
          failures.push({
            name: fullName,
            message: typeof f === "string" ? f : (f["@_message"] ?? f["#text"] ?? ""),
            stacktrace: typeof f === "string" ? undefined : (f["#text"] ?? undefined),
          });
        }
      }

      // Check for <error> elements
      if (tc.error) {
        const errList = Array.isArray(tc.error) ? tc.error : [tc.error];
        for (const e of errList) {
          failures.push({
            name: fullName,
            message: typeof e === "string" ? e : (e["@_message"] ?? e["#text"] ?? ""),
            stacktrace: typeof e === "string" ? undefined : (e["#text"] ?? undefined),
          });
        }
      }
    }
  }

  const passed = total - failed - skipped;

  return {
    pass: failed === 0,
    total,
    passed: Math.max(0, passed),
    failed,
    skipped,
    duration_ms: Math.round(durationSec * 1000),
    failures,
  };
}

/** Read a JUnit XML file and return normalized TestResult. */
export function parseJunitXmlFile(filePath: string): TestResult {
  const content = fs.readFileSync(filePath, "utf8");
  return parseJunitXml(content);
}
