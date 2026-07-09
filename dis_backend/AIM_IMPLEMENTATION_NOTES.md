# AIM DIS Content Ingestion Updates

## What changed

This codebase keeps the DIS pipeline common for all clients and adds a small AIM profile for safe AIM metadata enrichment.

Updated/added files:

- `config/clients/aim.yaml`
  - Added `aim_content_rules`.
  - Content team decisions such as final folder priority, excluded folders, visibility, and calendar-first mapping can now be changed in config.

- `config/settings.py`
  - Added `client_rules` to `TenantConfig`.
  - Client-specific rule blocks such as `aim_content_rules` are loaded at runtime.

- `services/client_profiles/__init__.py`
- `services/client_profiles/base.py`
- `services/client_profiles/aim.py`
  - Added client-profile metadata enrichment.
  - AIM profile handles file type, block/day, version, visibility, restricted files, and generation eligibility.

- `services/agents/metadata_tagging_agent.py`
  - Calls the client profile after common metadata tagging.

- `services/agents/content_unit_creation_agent.py`
  - Uses enriched `content_type` for unit type mapping where available.

- `services/context_retrieval.py`
  - Added dynamic filters for `block_id`, `day_id`, `visibility`, `use_for_blueprint`, `use_for_course_generation`, `restricted`, etc.
  - Added calendar-day hint matching so AIM quiz/project/study files can be retrieved by calendar mapping when filename does not contain the actual day.

- `api/routers/context.py`
  - Added wrapper endpoints:
    - `POST /v1/context/retrieve/blueprint`
    - `POST /v1/context/retrieve/course-generation`

## AIM ingestion metadata fields

Common fields produced for AIM files:

```json
{
  "client_id": "aim",
  "program_id": "aim",
  "course_id": "general_science_i",
  "block_id": "B1",
  "block_number": 1,
  "day_id": "B1D17",
  "day_number": 17,
  "source_relative_path": "Final PDFs/B1D17 - Weight and Balance - Day 4.pdf",
  "source_file_name": "B1D17 - Weight and Balance - Day 4.pdf",
  "source_file_type": "pdf",
  "content_type": "lesson_pdf",
  "document_type": "lesson_pdf",
  "title": "Weight and Balance Day 4",
  "topic": "Weight and Balance",
  "version": "pdf_final",
  "visibility": "student",
  "restricted": false,
  "status": "approved_candidate",
  "source_priority": 1,
  "use_for_blueprint": false,
  "use_for_course_generation": true,
  "calendar_mapping_required": false,
  "mapping_source_preference": "calendar_first_then_filename"
}
```

## Calendar-first mapping rule

For AIM, quiz, final exam, project, hangar activity, and study-question files should use calendar mapping as source of truth:

```yaml
calendar_mapping_strategy: calendar_first_then_filename
calendar_mapping_required_types:
  - quiz
  - final_exam
  - project
  - hangar_activity
  - study_questions
```

Filename day is stored only as a suggestion:

```json
{
  "suggested_day_from_filename": "B1D16",
  "mapped_day_source": "calendar_required"
}
```

The actual mapping should come from calendar/syllabus. During retrieval, if the file does not have `day_id`, DIS uses the requested calendar day text as hints to match related quiz/project/study files safely.

## Blueprint context retrieval endpoint

```http
POST /v1/context/retrieve/blueprint
```

Example request:

```json
{
  "request_id": "bp_aim_b1_001",
  "generation": {
    "type": "blueprint",
    "program_id": "aim",
    "course_id": "general_science_i",
    "block_id": "B1",
    "output_format": "json"
  },
  "context_input": {
    "goal": "Generate Block 1 blueprint for AIM",
    "block_title": "General Science I"
  },
  "filters": {
    "block_id": "B1"
  },
  "retrieval": {
    "top_k": 20,
    "token_budget": 12000,
    "min_score": 0.0
  }
}
```

Endpoint defaults:

```json
{
  "content_types": ["course_calendar", "syllabus"],
  "use_for_blueprint": true,
  "include_restricted": false
}
```

## Course generation context retrieval endpoint

```http
POST /v1/context/retrieve/course-generation
```

Example student-facing request:

```json
{
  "request_id": "course_aim_b1d17_001",
  "generation": {
    "type": "course_generation",
    "program_id": "aim",
    "course_id": "general_science_i",
    "block_id": "B1",
    "day_id": "B1D17",
    "output_format": "json"
  },
  "context_input": {
    "goal": "Generate student-facing course content for B1D17",
    "day_title": "Weight and Balance - Day 4",
    "topic": "Weight and Balance"
  },
  "filters": {
    "block_id": "B1",
    "day_id": "B1D17",
    "visibility": "student",
    "content_types": [
      "lesson_pdf",
      "slide_deck",
      "quiz",
      "project",
      "hangar_activity",
      "study_questions"
    ]
  },
  "retrieval": {
    "top_k": 30,
    "token_budget": 18000,
    "min_score": 0.0,
    "include_visual_summary": true
  }
}
```

Endpoint defaults:

```json
{
  "visibility": "student",
  "use_for_course_generation": true,
  "include_restricted": false
}
```

## Instructor-only retrieval

Only `client_admin` or `super_admin` can set:

```json
{
  "include_restricted": true
}
```

This is needed for answer keys or instructor guides. Normal users cannot retrieve those files.

## Validation note

I ran Python compile successfully:

```bash
python -m compileall -q config services api models main.py
```

The existing test suite has some pre-existing failures unrelated to this AIM change when run with `PYTHONPATH=.`. The import path must be set when running pytest from the unpacked folder.
