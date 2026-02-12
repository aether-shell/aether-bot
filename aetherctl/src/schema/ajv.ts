import Ajv2020, { type ValidateFunction, type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

export type AjvValidateFn = ValidateFunction;

export type AjvInstance = {
  compile: (schema: unknown) => AjvValidateFn;
  errorsText: (errors?: ErrorObject[] | null | undefined) => string;
};

export async function loadAjv(): Promise<AjvInstance> {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);

  return ajv as unknown as AjvInstance;
}
