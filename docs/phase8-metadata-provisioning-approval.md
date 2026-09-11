# Phase 8 — Metadata Provisioning Approval

**Status:** APPROVAL RECORDING ONLY. This commit does not implement Phase 8.

**Classification of each decision:** `APPROVE` | `REJECT` | `CHANGE: <value>`

No additional decisions are inferred beyond those listed below.

---

## 1. Baseline

| Item | Value |
|---|---|
| Repository | content-ai-studio |
| Branch | `feature/dis_metadata` |
| Starting HEAD | `ffd31a03f005b4c4a0c4b15d6e6780574311044b` |
| Starting subject | `docs: add phase 8 metadata provisioning discovery` |
| Working tree at start | CLEAN (`git status --short` empty) |

This record follows `docs/phase8-metadata-provisioning-discovery.md`. It does not change application code, frontend, CAS, DIS runtime, existing tenant YAML, `registry.py`, `metadata_schemas`, `source_ui`, client profiles, infrastructure, DB, S3, OpenSearch, or tests.

---

## 2. Approved Decisions

### DECISION-001 — CREATE VS UPDATE

**APPROVE**

CREATE ONLY for Phase 8 v1.

If the target tenant configuration already exists: **STOP**.

- Do not overwrite it.
- Do not silently update it.
- Do not reconcile it.

Future UPDATE / RECONCILE behavior requires a separate decision.

### DECISION-002 — TEMPLATE FAMILY

**APPROVE**

Use explicit template families:

- `minimal`
- `academic`
- `publishing`

Template family **MUST** be explicitly supplied.

- Do not infer the template family from client name.
- Do not guess.

### DECISION-003 — DOCUMENT PROCESSING

**REJECT** for Phase 8 v1.

Do not automatically provision `document_processing` rules.

Only metadata configuration is in scope.

If a tenant requires custom processing behavior, it remains a separate configuration/code decision.

### DECISION-004 — ACADEMIC TOPIC

**REJECT** automatic Topic provisioning.

Do not automatically add AIM Topic configuration to every academic tenant.

Topic is configured only when explicitly supplied as a tenant override.

Do not modify `DEFAULT_REGISTRY`.

### DECISION-005 — REFERENTIAL VALIDATION

**APPROVE** strict validation before writing.

The future generator must validate:

- YAML structure
- required identity
- namespace
- metadata schema structure
- metadata framework structure
- valid Field Registry promotion targets
- duplicate metadata fields
- `source_ui` references
- valid `source_ui` controls
- profile reference if supplied

Invalid configuration must fail **BEFORE** writing the tenant file.

### DECISION-006 — YAML SHAPE

**APPROVE**

Generated tenant configuration uses the existing `client:` wrapper shape.

Do not generate the legacy 3-field top-level shape.

Verify compatibility with `TenantConfig` loading before implementation.

### DECISION-007 — GENERATED CONFIGURATION IN GIT

**APPROVE**

Generated tenant YAML is repository-managed and must be committed to Git when provisioning succeeds.

Provisioning itself must not automatically push to the remote.

Operator reviews and commits/pushes through the normal workflow.

### DECISION-008 — STORES / SECRETS

**REJECT** secret generation.

- Do not copy credentials from existing AIM/Cengage configurations.
- Do not put secrets into generated tenant YAML.

Stores should remain disabled unless explicitly configured through the existing secure mechanism.

### DECISION-009 — TEMPLATE SELECTION

**APPROVE**

Template family is an explicit provisioning input.

Conceptual invocation:

```text
provision(client, template="academic", overrides={...})
```

Do not infer template from:

- client name
- display name
- slug
- domain

### DECISION-010 — PROVISIONING STAMP

**APPROVE** lightweight template/version metadata **ONLY if** it:

- does not break existing `TenantConfig` loading
- does not become a new metadata authority
- does not affect runtime behavior
- is useful for identifying generated configuration

Do not create a complex versioning system.

### DECISION-011 — NAMESPACE

**APPROVE**

Preserve the existing namespace convention.

Do not change namespace format as part of Phase 8.

Any namespace convention change requires a separate decision.

### DECISION-012 — DEFAULT REGISTRY

**REJECT** copying `DEFAULT_REGISTRY` fields into generated tenant YAML.

Generated configuration contains only tenant-specific `metadata_framework` overrides.

Runtime continues to resolve:

```text
DEFAULT_REGISTRY
+
tenant overlay
```

Do not create per-tenant copies of `DEFAULT_FIELDS`.

### DECISION-013 — METADATA AUTHORITY

**APPROVE** existing four-layer architecture.

Keep:

| Layer | Responsibility |
|---|---|
| `metadata_schemas` | domain definition / validation |
| `retrieval.source_ui` | presentation |
| `metadata_framework` / Field Registry | projection |
| client profiles | inference |

Do not merge these layers.

### DECISION-014 — EXISTING TENANTS

**REJECT** migration of existing tenants.

Phase 8 automation applies to **NEW** tenants only.

Do not rewrite:

- AIM
- Cengage
- Academian

### DECISION-015 — DATABASE

**REJECT** new database-backed metadata configuration.

Continue repository/YAML-based tenant configuration.

### DECISION-016 — ADMIN UI

**REJECT** new admin UI.

Phase 8 should use the existing provisioning mechanism / CLI/service path.

### DECISION-017 — NEW API

**REJECT** creation of a new metadata provisioning API unless the existing CAS provisioning path absolutely requires it.

Prefer extending the existing provisioning mechanism.

### DECISION-018 — INFRASTRUCTURE

**REJECT** infrastructure provisioning.

Out of scope:

- AWS
- S3
- OpenSearch
- Cognito
- IAM
- Terraform
- DB instances
- networking

Phase 8 is metadata configuration provisioning only.

### DECISION-019 — IDEMPOTENCY

**APPROVE** deterministic CREATE behavior.

For a new tenant:

```text
same input → same generated configuration
```

If the target file already exists: **STOP** instead of overwriting.

Allowlist behavior must remain idempotent.

### DECISION-020 — ROLLBACK

**APPROVE**

Because Phase 8 is CREATE ONLY, rollback is primarily:

remove the newly generated tenant configuration and reverse the associated allowlist/configuration change through the normal Git workflow.

Do not introduce a separate rollback service.

---

## 3. Approved Architecture

Approved conceptual architecture:

```text
NEW CLIENT
    +
EXPLICIT TEMPLATE
    +
CLIENT OVERRIDES
        ↓
STRICT VALIDATION
        ↓
GENERATED client: YAML
        ↓
Git review/commit
        ↓
existing TenantConfig
        ↓
runtime
```

Templates: `minimal` | `academic` | `publishing`

The generated configuration **MUST NOT** become a new authority.

Runtime continues to load generated files through the existing `TenantRegistry` / `TenantConfig` path.

---

## 4. Tenant Configuration Model

Unchanged storage model: static YAML under `dis_backend/config/clients/`, versioned in Git, loaded at runtime.

Generated files:

- use the `client:` wrapper shape (**DECISION-006**)
- are CREATE ONLY (**DECISION-001**, **DECISION-019**)
- contain tenant identity plus template/override metadata configuration
- contain only tenant-specific `metadata_framework` overlays, never a copy of `DEFAULT_FIELDS` (**DECISION-012**)
- do not include secrets or enabled stores unless those are configured through the existing secure mechanism (**DECISION-008**)
- may include lightweight provisioning stamp metadata only under the constraints of **DECISION-010**
- preserve existing namespace convention (**DECISION-011**)

The four authorities remain separate (**DECISION-013**). Templates generate configuration for those layers; they are not a fifth authority.

---

## 5. Template Families

| Family | Role |
|---|---|
| `minimal` | Explicit family. Must be supplied; not inferred. |
| `academic` | Explicit family. Must be supplied; not inferred. Does **not** automatically include AIM Topic (**DECISION-004**). |
| `publishing` | Explicit family. Must be supplied; not inferred. |

Template family is a required provisioning input (**DECISION-002**, **DECISION-009**).

Topic, if needed, is a tenant override — not part of the academic family default.

`document_processing` is not part of any family in Phase 8 v1 (**DECISION-003**).

---

## 6. Validation Requirements

**DECISION-005:** invalid configuration fails **before** the tenant file is written.

Required checks:

1. YAML structure
2. Required identity
3. Namespace
4. Metadata schema structure
5. Metadata framework structure
6. Valid Field Registry promotion targets
7. Duplicate metadata fields
8. `source_ui` references
9. Valid `source_ui` controls
10. Profile reference if supplied

Validation must not merge authorities. It checks relationships between existing layers.

Compatibility of the `client:` wrapper with `TenantConfig` loading must be verified before implementation (**DECISION-006**).

---

## 7. CREATE ONLY Behavior

**DECISION-001** and **DECISION-019**:

| Condition | Behavior |
|---|---|
| Target tenant configuration does **not** exist | Generate deterministically (`same input → same configuration`) |
| Target tenant configuration **already exists** | **STOP**. No overwrite. No silent update. No reconcile. |
| Allowlist | Remain idempotent (append if missing; do not duplicate) |

UPDATE / RECONCILE is not authorized in Phase 8 v1.

---

## 8. Git Workflow

**DECISION-007**, **DECISION-020**:

1. Provisioning generates tenant YAML locally (after validation).
2. Provisioning does **not** push to the remote.
3. Operator reviews and commits/pushes through the normal Git workflow.
4. Generated tenant YAML is repository-managed.
5. Rollback: remove the newly generated tenant configuration and reverse the associated allowlist/configuration change through the normal Git workflow.
6. No separate rollback service.

---

## 9. Security / Secrets

**DECISION-008**, **DECISION-018**:

- Do not copy AIM/Cengage credentials into generated YAML.
- Do not put secrets into generated tenant YAML.
- Stores remain disabled unless explicitly configured through the existing secure mechanism.
- Do not provision AWS, S3, OpenSearch, Cognito, IAM, Terraform, DB instances, or networking.

---

## 10. Existing Tenant Boundary

**DECISION-014**: Phase 8 automation applies to **NEW** tenants only.

Do not rewrite AIM, Cengage, or Academian.

Do not migrate existing tenant configuration onto templates.

---

## 11. Explicit Non-Goals

This approval does **not** authorize:

- master metadata registry
- `metadata_schemas` → Field Registry automatic merge
- Field Registry → `metadata_schemas` merge
- database-backed metadata configuration
- admin UI
- new metadata API (unless the existing CAS provisioning path absolutely requires it — **DECISION-017**)
- OpenSearch redesign
- AWS provisioning
- S3 provisioning
- DB provisioning
- Terraform
- PipelineState
- TaxonomyRegistry
- upload hard-fail
- existing tenant migration
- hot reload
- client profile redesign
- document processing redesign
- secret provisioning
- `DEFAULT_REGISTRY` duplication

---

## 12. Implementation Gate

This commit records decisions only.

It **DOES NOT** authorize implementation beyond the approved scope.

Do not implement Phase 8 until explicit implementation instructions are given.

Wait for review and explicit implementation authorization.

---

*End of Phase 8 approval record.*
