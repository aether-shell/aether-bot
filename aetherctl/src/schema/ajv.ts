import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

export type AjvValidateFn = ((data: unknown) => boolean) & { errors?: unknown };

export type AjvInstance = {
  compile: (schema: unknown) => AjvValidateFn;
  errorsText: (errors: unknown) => string;
};

export async function loadAjv(): Promise<AjvInstance> {
  const AjvCtor = Ajv2020 as unknown as { new (opts: unknown): AjvInstance };
  const add = addFormats as unknown as (ajv: AjvInstance) => void;

  const ajv = new AjvCtor({ allErrors: true, strict: true });
  add(ajv);

  return ajv;
}
