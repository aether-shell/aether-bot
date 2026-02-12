import path from "node:path";
import { parseJunitXmlFile } from "./junit-xml.js";
import { passthroughJson } from "./passthrough.js";
import type { AdapterOutput, TestResult } from "../types/adapter-output.js";

const ADAPTER_VERSION = "1.0.0";

export type SourceFormat = "junit_xml" | "json" | "tap" | "custom";

/** Detect source format from file extension. */
function detectFormat(filePath: string): SourceFormat {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".xml") return "junit_xml";
  if (ext === ".json") return "json";
  return "custom";
}

/**
 * Adapter entry point — routes to the appropriate parser based on format.
 *
 * @param filePath - Path to the test result file.
 * @param format - Explicit format override. Auto-detected from extension if omitted.
 */
export function adaptTestResult(filePath: string, format?: SourceFormat): AdapterOutput {
  const sourceFormat = format ?? detectFormat(filePath);
  let result: TestResult;

  switch (sourceFormat) {
    case "junit_xml":
      result = parseJunitXmlFile(filePath);
      break;
    case "json":
      result = passthroughJson(filePath);
      break;
    default:
      throw new Error(`Unsupported adapter format: ${sourceFormat}`);
  }

  return {
    adapter_version: ADAPTER_VERSION,
    source_format: sourceFormat,
    source_file: path.basename(filePath),
    converted_at: new Date().toISOString(),
    result,
  };
}
