# Pilot prompt comparison

Comparison recorded on September 26, 2026. This evaluates the comparison
machinery with stored synthetic responses. It does not measure model quality.

## Controlled conditions

- Prompt versions: `pilot-drafts.v1` and `pilot-drafts.v2`.
- Schema version: `pilot-drafts.v1`.
- Identical synthetic input, marketing-round purpose, reference time
  (`2026-10-26T10:00:00Z`), model label (`test-structured-model`), timeout
  (12 seconds), maximum output (1,200 tokens), and zero automatic retries.
- Identical stored valid response for the pass case. Targeted mutations cover an
  altered price, expired offer claim, and altered ticket URL.
- Hard checks run through `compare_prompt_versions`; qualitative criteria are
  tone, clarity, channel fit, call to action, and correction effort.

## Result

| Prompt | Synthetic hard checks | Qualitative assessment |
| --- | --- | --- |
| `pilot-drafts.v1` | Pass for the valid stored response; targeted mutations fail | Pending genuine output and human review |
| `pilot-drafts.v2` | Pass for the valid stored response; targeted mutations fail | Pending genuine output and human review |

No hard-check regression is present in the stored comparison. Because both
versions receive the same synthetic response, the result verifies isolation,
fact checking, and regression reporting only. It cannot show whether V2 improves
tone or instruction following. No genuine OpenAI output or credential was used,
so qualitative scores and correction-effort claims remain pending.

Reproduce the controlled comparison and all generation failure cases with:

```sh
uv run --locked python -m pytest tests/test_generation.py
```

The separately documented
[sanitized Mal-Yoga observation](fixtures/mal-yoga-sanitized-snapshot.json) is
context for a later deliberate comparison. Its availability count is a dated
observation, not a reusable generation fact. The historical incomplete
reconstruction under `examples/workshops/` remains unchanged.
