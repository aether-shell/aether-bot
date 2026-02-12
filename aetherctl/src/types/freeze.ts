/** Freeze snapshot — records the state of main at incubation start. (V3 §6.5) */
export type Freeze = {
  incubation_id: string;
  created_at: string;
  main_sha: string;
  lockfile_hash: string;
  schema_version: string;
  config_template_version?: string;
  baseline_ref?: string | null;
};
