import { describe, expect, it } from "vitest";
import path from "node:path";
import { parseJunitXml, parseJunitXmlFile } from "../src/adapter/junit-xml.js";
import { passthroughJson } from "../src/adapter/passthrough.js";
import { adaptTestResult } from "../src/adapter/adapter.js";
import { createRegistry } from "../src/schema/registry.js";

const FIXTURES = path.resolve(import.meta.dirname, "../fixtures");
const SCHEMA_DIR = path.resolve(import.meta.dirname, "../schemas");

describe("junit-xml adapter", () => {
  it("parses sample JUnit XML correctly", () => {
    const result = parseJunitXmlFile(path.join(FIXTURES, "sample-junit.xml"));
    expect(result.total).toBe(5);
    expect(result.failed).toBe(1);
    expect(result.skipped).toBe(1);
    expect(result.passed).toBe(3);
    expect(result.pass).toBe(false);
    expect(result.duration_ms).toBe(2345);
    expect(result.failures).toHaveLength(1);
    expect(result.failures![0].name).toContain("security");
    expect(result.failures![0].message).toContain("Expected true");
  });

  it("handles empty XML gracefully", () => {
    const result = parseJunitXml('<?xml version="1.0"?><root/>');
    expect(result.total).toBe(0);
    expect(result.pass).toBe(true);
    expect(result.failures).toEqual([]);
  });

  it("handles single testsuite without wrapper", () => {
    const xml = `<?xml version="1.0"?>
      <testsuite name="unit" tests="2" failures="0" errors="0" skipped="0" time="0.5">
        <testcase name="test1" time="0.2"/>
        <testcase name="test2" time="0.3"/>
      </testsuite>`;
    const result = parseJunitXml(xml);
    expect(result.total).toBe(2);
    expect(result.passed).toBe(2);
    expect(result.pass).toBe(true);
  });
});

describe("passthrough adapter", () => {
  it("reads JSON test result file", () => {
    const result = passthroughJson(path.join(FIXTURES, "sample-result.json"));
    expect(result.pass).toBe(true);
    expect(result.total).toBe(10);
    expect(result.passed).toBe(9);
    expect(result.skipped).toBe(1);
  });
});

describe("adapter router", () => {
  it("auto-detects XML format from extension", () => {
    const output = adaptTestResult(path.join(FIXTURES, "sample-junit.xml"));
    expect(output.source_format).toBe("junit_xml");
    expect(output.adapter_version).toBe("1.0.0");
    expect(output.result.total).toBe(5);
  });

  it("auto-detects JSON format from extension", () => {
    const output = adaptTestResult(path.join(FIXTURES, "sample-result.json"));
    expect(output.source_format).toBe("json");
    expect(output.result.pass).toBe(true);
  });

  it("throws on unsupported format", () => {
    expect(() => adaptTestResult("/fake/file.tap", "tap")).toThrow("Unsupported");
  });

  it("adapter output passes schema validation", async () => {
    const registry = await createRegistry(SCHEMA_DIR);
    const output = adaptTestResult(path.join(FIXTURES, "sample-junit.xml"));
    const { valid, errors } = await registry.validate("adapter-output", output);
    expect(errors).toBeNull();
    expect(valid).toBe(true);
  });
});
