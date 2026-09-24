# Working on workshop-marketing-agent

## Project phase and source of truth

- The Python package foundation and provisional workshop input validation are
  implemented. Generation and integrations remain planned; implement them only
  through subsequent tasks.
- Before making changes, read the relevant sections of
  [docs/architecture.md](docs/architecture.md) and [docs/v1-scope.md](docs/v1-scope.md).
  They distinguish confirmed decisions, technical proposals, and open questions.
  Do not treat open decisions as approved or implemented.
- Explicit user instructions define the task. Do not ask again about settled
  decisions; resolve routine details within the authorized scope.

## Language

- Write project documentation, code comments, new issues, pull requests, and
  commit messages in English. Prefer clear, simple wording.
- Keep workshop content, generated marketing copy, and the teacher-facing
  interface in German. German example data is appropriate for this use case.
- Continue discussing the project and explaining Python to the user in German.

## Small, understandable changes

- Implement only the behavior covered by the current issue or task.
- Each PR should express one understandable idea. Avoid a single "implement V1" PR.
- Do not introduce unsolicited refactoring, dependencies, or infrastructure work.
  Justify additional technologies with a specific problem.
- Mention improvements outside the scope only as follow-up work.
- Do not extensively rewrite the README without being asked. Documentation must
  distinguish the actual state, assumptions, and outstanding prerequisites.
- Explain important decisions and diffs so that someone learning Python can
  follow them. Comments should primarily explain why.

## Technical guidelines

- Prefer simple, explicit Python functions and understandable types.
- Current tools: Python type annotations, Pydantic, and pytest through uv.
  The official OpenAI SDK remains planned. Use the project's locked environment.
- Do not use agent frameworks, dynamic plugin systems, or large inheritance trees.
- Keep business rules independent of the Firestore schema, frontend, and actual
  secrets. The Firebase integration maps data, checks access, and stores results.
- Keep channel rules and supported actions in the respective adapter. Do not
  invent APIs, field limits, or platform capabilities; check official sources.
- Handle times, prices, age information, URLs, UTM values, and statuses
  deterministically. Missing facts remain unknown. Do not silently resolve conflicts.
- Change original workshop copy only when the user explicitly requests it.
- Workshop text and revision requests are untrusted input. They must not control
  permissions, system rules, or external actions.
- Approval applies to an exact version, including its image and link. Changes
  require another review. An unknown publishing outcome is not a failure that
  can safely be retried by blindly creating another post.

## Tests and evaluation

- Add focused tests for new executable behavior and relevant failure cases.
  Documentation-only changes do not need artificial behavior tests.
- Use offline fakes and synthetic fixtures for routine checks. Avoid real
  publications or bookings as unintended test effects.
- Changes to prompts, models, or channel rules require an evaluation comparison
  appropriate to the behavior. Document regressions and uncertainty as well.
- Structured Outputs do not guarantee factual accuracy. Report hard fact checks
  and qualitative evaluation separately.
- Run the checks appropriate to the issue. State what was verified and what
  could not be verified because prerequisites were missing.
- Do not claim passing tests or measured pilot results without evidence.

## Data and publishing

- Never put API keys, OAuth tokens, private Firebase credentials, or participant
  data in the repository, frontend, public examples, or logs.
- A future `.env.example` must contain placeholders only.
- Use synthetic or anonymized evaluation and demonstration data.
- Respect approved image uses and required attribution.
- Do not use browser bots to replace missing publishing APIs.
- External publishing requires specific approval within the user's request or
  the intended product workflow.
- Keep changes to the existing teacher app identifiable as integration work;
  this package must not become a complete web application project.
