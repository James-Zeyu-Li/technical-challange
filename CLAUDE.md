# Project Guidelines

## AI behavior — avoid these three failure modes
- **Hallucination**: never state facts, APIs, file paths, or behavior you have not verified. If unsure, say so or check the code/docs first.
- **Sycophancy**: do not agree with the user or praise an approach just to be agreeable. Give honest technical assessments, including disagreement, when warranted.
- **Deception**: never claim work is done, tested, or correct when it isn't. Do not hide incomplete implementations, skipped steps, or known issues — surface them explicitly.

## Code design principles
All code in this project must follow:
- **Object-Oriented Design**: model domain concepts as classes with clear responsibilities and interfaces.
- **SOLID**:
  - Single Responsibility — each class/module has one reason to change.
  - Open/Closed — extend behavior via new code, not by modifying stable existing code.
  - Liskov Substitution — subtypes must be usable wherever their base type is expected.
  - Interface Segregation — prefer small, specific interfaces over large general ones.
  - Dependency Inversion — depend on abstractions, not concrete implementations.
- **DRY** (Don't Repeat Yourself): no duplicated logic; extract shared behavior.
- **KISS** (Keep It Simple, Stupid): prefer the simplest design that works; avoid unnecessary complexity.
- **YAGNI** (You Aren't Gonna Need It): don't build for hypothetical future requirements; implement only what's needed now.

## Spec adherence
Before changing pipeline/retry/dedup/failure-handling behavior, read [aggregator/SPEC.md](aggregator/SPEC.md) — those decisions are confirmed. If a decision needs to change, update SPEC.md in the same change, don't just change the code.
