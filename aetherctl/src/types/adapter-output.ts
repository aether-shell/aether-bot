/** Adapter output — normalized test result from an Artifact Adapter. (V3 §6.8) */
export type TestFailure = {
  name: string;
  message: string;
  stacktrace?: string;
};

export type TestResult = {
  pass: boolean;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  failures?: TestFailure[];
};

export type AdapterOutput = {
  adapter_version: string;
  source_format: "junit_xml" | "json" | "tap" | "custom";
  source_file: string;
  converted_at: string;
  result: TestResult;
};
