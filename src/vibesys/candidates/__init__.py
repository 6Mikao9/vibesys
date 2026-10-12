"""Candidate lifecycle policy shared by the searches that look for a better revision.

What counts as a candidate worth keeping, which one wins, and how a run ends are VibeSys
policy, not core protocol, and every search that proposes candidates needs the same answers:

- settlement: the assessments, eligibility and retention a finished attempt asserts to core;
- the input baseline: a small machine that measures the starting revision once and gates
  candidates on the reading;
- selection: choose the winner, propose its adoption, finish the run.

Import only `vibesys.candidates.api`.
"""
