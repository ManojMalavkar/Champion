# Track T2 - HPC

Branch `track/hpc`, package `champion/specialists/hpc/`, tests `tests/hpc/`. Status: T2.0 can start now; T2.1 waits for M2.

## Goal
`hpc_agent`: port of the existing HPC agent (`mscript-git/Final_pakage/mscripts_data/HPC_Agent`, read only there). Slurm only through `mjob`, called as a trusted program (M2), never direct `ssh`, `sbatch`, `scancel`. Uses Champion's configured model; the old agent's `certs/` are not used.

## Sub-steps
- [ ] T2.0 `*_jobid` file parser as a plain function (from `get_job_details_from_file`), tests only.
- [ ] T2.1 Read only (needs M2): `jobstat`, `single_jobstatus_by_id`, `single_jobstatus_by_path`, `list_completed_job_files`, job details from file. Verify: a running and a finished job.
- [ ] T2.2 Downloads (edit, into an edit folder, in `/changes`): `download_running_job_result_by_id`, `download_completed_job_result_by_id`.
- [ ] T2.3 Stop jobs (approval every time, own jobs only): `job_kill_by_id`, `job_delete_by_id`.
- [ ] T2.4 FEMZIP: `convert_d3plot_to_femzip`, `convert_d3part_to_femzip`.
- [ ] T2.5 Later: submit (approval every time); skill `hpc.job_submission` (needs T5).

## Decisions
* No submit in the first version (the existing agent has none). `submit_suggestion` / `list_suggestions` not ported (audit log instead).

## Core requests (CR)
None yet.

## Findings and live checks
None yet.
