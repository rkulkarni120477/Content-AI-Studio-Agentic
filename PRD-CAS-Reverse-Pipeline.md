**PRODUCT REQUIREMENTS DOCUMENT**

**Content AI Studio - Reverse Pipeline**

Course Import & Round-Trip AI Authoring

v1.0 · Draft for review · July 2026 · Owner: Product

**The one-liner**

Today Content AI Studio authors courses forward (Style → CDD → Blueprint → Generate → Editor → Export). The Reverse Pipeline lets a user drop in an existing Canvas IMSCC package; CAS reconstructs the Editor exactly, then AI reverse-generates the Blueprint and CDD - turning a legacy course into a fully AI-editable, round-trippable project. One product, two on-ramps: AI-first and legacy-modernization.

# 1 · Why we are building this

Institutions already have thousands of Canvas courses. Today they can only benefit from CAS by rebuilding a course from scratch - a hard sell. The Reverse Pipeline removes that barrier: import once, and the course inherits the entire CAS toolchain (AI regeneration, style control, approval workflow, versioning, collaboration, export). It converts our biggest adoption blocker into our strongest expansion motion.

**Core USP:** true round-trip AI course authoring. A course can originate from AI or from an imported package, and thereafter be edited from CDD, Blueprint, Generate, or Editor while every artifact stays synchronized.

# 2 · Who it is for & the journey

**Primary user:** an instructional designer or faculty member migrating existing Canvas courses. Secondary: reviewers and admins governing the migrated content.

The happy path adds two clicks to the existing product:

- Create Project → choose **"Import Existing Course"** (new option beside "New Course").
- Upload the IMSCC package; CAS validates it and starts a background import job.
- A live progress screen shows Extract → Parse → Reconstruct Editor → Reverse-generate Blueprint → Reverse-generate CDD → (optional) Style analysis.
- The course opens in the normal workspace - every tab already populated. The user edits from any stage and exports back to IMSCC.

# 3 · Scope

| **In scope (v1)**                                                                          | **Out of scope (later)**                           |
| ------------------------------------------------------------------------------------------ | -------------------------------------------------- |
| Canvas IMSCC 1.1 import (modules, pages, assignments, QTI quizzes, discussions, resources) | SCORM, Moodle, Brightspace imports                 |
| High-fidelity Editor reconstruction + provenance map                                       | AI modernization suggestions (queued, not applied) |
| Reverse-generation of Blueprint and CDD                                                    | Standards mapping & course comparison              |
| Optional style analysis of imported content                                                | Bulk / batch migration of many courses at once     |
| Round-trip export via the existing IMSCC engine                                            | Automated quality scoring                          |

# 4 · What it must do

- **Upload & validate** IMSCC packages, with clear pre-flight feedback (structure counts, unsupported items flagged).
- **Extract & parse** the Canvas manifest, modules, pages, assignments, quizzes, discussions and resources into a normalized internal course model.
- **Reconstruct the Editor** so the course hierarchy and content appear exactly as the original.
- **Reverse-generate** the Blueprint (per-module objectives, key concepts, assessments) and then the CDD (course-wide audience, outcomes, tone, quality standards), each constrained by the layer above.
- **Edit & regenerate** any lesson, module, or the full course from CDD, Blueprint, Generate, or Editor.
- **Bidirectional sync** - edits to any artifact intelligently regenerate the linked artifacts so the course stays internally consistent.
- **Export** back to IMSCC using the existing CAS export engine, preserving fidelity via the provenance map.

**Reuse, don't rebuild:** the import subsystem is the only new part. It hands off to the unchanged CAS services - AI Generation Engine, Prompt & Source Libraries, Editor, Version History, Collaboration, Approval Workflow, Export Engine, and the multi-tenant/RBAC foundation.

# 5 · Recommended refinements (beyond the brief)

The original PRD is sound. Four additions materially de-risk it and are reflected in the mockups:

- **Provenance map as a first-class artifact.** Store an explicit Canvas-item ↔ CAS-block mapping at import. It is what makes high-fidelity re-export (≥95% structure) achievable and lets the user trust the round trip.
- **Reconstruct first, generate second - as separate, resumable stages.** The Editor should be usable the moment reconstruction finishes, even before Blueprint/CDD generation completes. This protects the "≥95% structure" metric independently of AI quality and gives instant value.
- **A visible sync/review queue.** Regeneration should be reviewable, not silent. Surface "N sections need sync" so designers approve downstream regenerations rather than being surprised by them (shown as the amber banner in the mockup).
- **Pluggable parser + confidence flags.** Design the Canvas parser behind an interface so SCORM/Moodle slot in later, and flag low-confidence reconstructions (e.g., unsupported LTI tools) for manual review instead of failing the whole import.

# 6 · Non-functional requirements

| **Requirement** | **Target**                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------ |
| Fidelity        | High-fidelity reconstruction; provenance map covers every reconstructed item                           |
| Scale           | Large packages (up to ~2 GB) processed as background jobs                                              |
| Resilience      | Error recovery - failed items retry, then flag for manual review; import never blocks the whole course |
| Extensibility   | Parser is pluggable for future formats                                                                 |
| Security        | Strict tenant isolation; imports honor existing RBAC                                                   |

# 7 · How we'll know it works

| **Metric**                                       | **Target** |
| ------------------------------------------------ | ---------- |
| Structure reconstruction accuracy                | ≥ 95%      |
| Content fidelity                                 | ≥ 90%      |
| Reverse-generated Blueprint / CDD quality        | ≥ 90%      |
| Reduction in migration effort vs. manual rebuild | ≥ 80%      |
| Round-trip editing supported end to end          | Yes        |

# 8 · Architecture at a glance

IMSCC → Import Service → Extraction Engine → Canvas Parser → Internal Course Model → Editor Builder → Blueprint AI → CDD AI → CAS Project. See the accompanying architecture and flow diagrams and the interactive HTML mockup for the full picture.

**Companion deliverables:** an interactive HTML mockup of every screen (login → import → workspace → export) and three diagrams (bidirectional pipeline, system architecture, import sequence).