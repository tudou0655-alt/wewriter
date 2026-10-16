# Cross-review prompt

> Act as reviewer, not implementer. Read the repository instructions, requirements, plan, task `[T-XXX]`, current handoff, and the relevant Git diff. Focus on correctness, regressions, security, missing tests, and violated scope. Do not edit application files. Write findings using `REVIEW-TEMPLATE.md` to a new file under `docs/collab/REVIEWS/`, highest severity first, with precise file/line references. If there are no findings, say so and list any validation gaps. Do not commit or push.
