# RunFilter uses additive boolean knobs over union sentinels

- **Status**: accepted

## Context

`RunFilter` (`backend/app/schemas/ingestion.py`) is the filter object passed to ingestion runs, discovery runs, and the suggest pipeline. It selects documents by various dimensions — class assignment, confidence, staging state, dates.

Two filter dimensions emerged that don't fit cleanly into the existing `document_class_ids: list[UUID] | None`:

1. **Unclassified docs** — `Document.document_class_id IS NULL`. The natural rerun target for "classify the docs that didn't get a class first time."
2. **Low-confidence classifications** — `classification_confidence <= X`. The natural rerun target for "reclassify the doubtful ones."
3. **Staged docs** — `Document.staged = true` (see [2026-05-12 staged documents](2026-05-12-staged-documents.md)).

Three options for expressing these:

- (A) Overload `document_class_ids`: `None` means "unclassified only", `[]` means "all", `[uuids]` means "those classes."
- (B) Sentinel in union: `document_class_ids: list[UUID] | Literal["unclassified"] | None`.
- (C) Separate boolean fields: `include_unclassified: bool`, `max_classification_confidence: float | None`, etc.

## Decision

(C) Separate booleans, with two further rules:

- **Class-related clauses are OR-combined** inside a single OR group. `document_class_ids=[A]`, `include_unclassified=true`, `max_classification_confidence=0.6` together mean "class A OR unclassified OR confidence ≤ 0.6." This matches the natural English of rerun-targeting expressions ("the bad ones plus the unclassified ones").
- **Staging is an AND clause outside the OR group.** Staged is an independent dimension from class assignment. `staged_only=true` is the exception: it short-circuits the class group entirely, since staged docs have no class and combining the two would be incoherent.

Truth table for the staging knobs:

| `document_class_ids` | `include_staged` | `staged_only` | meaning |
|---|---|---|---|
| `None` / omitted | false | false | all unstaged docs (ingestion default) |
| `None` | false | true | only staged docs (promote path) |
| `[A, B]` | false | false | unstaged docs of class A or B |
| `[A, B]` | true | false | docs of class A or B, both staged and unstaged |

Class-OR-group examples (assume not-staged, since these target processed docs):

| filter | meaning |
|---|---|
| `{include_unclassified: true}` | docs with no class assigned |
| `{include_unclassified: true, max_classification_confidence: 0.6}` | "doubtful" set: unclassified OR confidence ≤ 0.6 |
| `{document_class_ids: [A], include_unclassified: true}` | class A OR unclassified |

## Alternatives considered

**(A) Overload `document_class_ids` with `None` for unclassified.** Reads as the inverse of the same-typed `document_ids: list | None`, which uses the standard "None = no filter, list = match" convention. Two filters with identical type signatures meaning opposite things is the kind of footgun that survives until someone has a production incident. Also: JSON clients can't reliably distinguish `null` from an omitted field, so "I forgot to set this" silently changes meaning. Rejected.

**(B) `Literal["unclassified"]` sentinel in the union.** Self-documenting, type-checked, avoids the magic-None problem. Still fails the composability test: can't easily express "class A OR unclassified" because the field is already taken by the sentinel. Would need a second field anyway. Rejected as the worst of both worlds.

**`min_confidence` instead of `max_classification_confidence`.** Original name in early sketches. Renamed because the semantic is an *upper bound* on confidence (we want the doubtful ones whose confidence is below the threshold), and `min_X` reads in filter contexts as "include only docs with at least X" — opposite of the actual behavior. The long name is verbose but unambiguous.

## Consequences

**Easier:**
- Adding a new filter dimension is a new field with no schema-level ripple effects.
- Defaults are unambiguous: a Pydantic-default `False` or `None` means "not constraining on this dimension," uniformly.
- Filters compose: a single endpoint accepts any combination without special-casing.

**Harder:**
- The schema has a growing list of boolean filter knobs. Some have non-obvious semantics (`staged_only` short-circuits; `include_staged` is additive). Field-level docstrings carry the load.
- The OR-vs-AND grouping is implicit in the runner code (`_select_documents`), not in the schema. A reader of `RunFilter` alone doesn't see that class-related fields collapse into one OR clause. Mitigation: code comment in the selector spells it out.
- "Class group short-circuits when `staged_only=true`" is a behavioral rule that lives in two places (ingestion runner, discovery runner). If a third caller emerges, the rule has to be applied there too.

**To revisit if reversed:** the rule "class-related clauses are OR-combined" affects backward compatibility. Switching to AND silently changes what existing API consumers' filter dicts mean. Any change to the combining semantics requires API versioning.
