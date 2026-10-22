# Evaluation admission investigation

Base: `f70745460476c9aa099b1d50c943aa596400189c`.
Branch: `fix/profile-admission-order`. No PR. No device commands.

## Evidence and diagnosis

Read-only evidence: the supplied run's `logs/core-events.jsonl`,
`receipts/evaluation-jobs`, `receipts/operations`, and `core-evaluation-tries`.
No experiment files were changed. No private machine identifiers are reproduced here.

The reported trace does **not** establish FIFO overtaking:

- The input starts accuracy at journal sequence 28 (11:28:33Z), then benchmark
  at 393 (11:34:30Z), ending at 768 (11:38:58Z).
- The first candidate validation was submitted at 11:33:24.941295Z;
  three more validations arrived at 11:34:14Z, 11:37:12Z and 11:38:10Z.
- The profile was submitted at 11:39:08.271049Z. Its durable request has one
  `profile` stage and no sequence admission lease.
- The first candidate's durable request has purpose `local-validation`, stages
  `accuracy, benchmark`, and no sequence lease. Its benchmark starts at
  sequence 1348 (11:48:42.425930Z), ending at 1716 (11:53:20.963030Z).
  Thus this command predates the profile in submission FIFO; it is not proof
  that a later official sequence overtook the profile.
- Queue-tool observations report one executor lease running (including at
  sequence 1778), not two device commands. Search slots and executor leases
  are different counts. The profile is already bound in the search queue;
  subsequent planner receipts show it in `measuring` rather than `waiting`.

Code owners: `search/_queue.py` applies planner ranking before binding a
workstream. Agent validations submit directly to the executor. The ranking
is not an executor priority field. `_polling_evaluation.py` admits PROFILE,
ACCURACY and BENCHMARK through the same `EvaluationAdmission` FIFO. No
exclusive or whole-host profile lease exists.

A separate starvation mechanism exists: `_polling_evaluation.py` reserves a
sequence lease after a completed nonfinal member and lets every successor
bypass `acquire`. A queued independent job can then wait through arbitrarily
many successors or an indefinite pause before the successor is submitted.
The supplied requests have no such lease, so this mechanism must not be
presented as the proven cause of this particular trace.

## Planned correction

Make an idle retained lease yield to waiting jobs, including jobs arriving
between sequence members. Retain the uncontended fast path and preserve the
chosen order inside each request. All independent jobs and reacquiring
sequences use submission FIFO. No strategy-specific order or cap.

Verification, regression evidence, final design and resume notes follow in the
completion commit.
