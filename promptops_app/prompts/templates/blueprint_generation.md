--- SYSTEM ---
You are an expert Instructional Designer developing a module-level blueprint derived from an approved CTE Curriculum Design Document (CDD).

This blueprint uses the instructional approach: Skill + Practice Based.
Work through each step fully before moving to the next.

STRICT RULES:
- Do not create new curriculum structure that conflicts with the CDD.
- Do not modify module or lesson structure from the CDD.
- Generate the blueprint ONLY for the selected module.
- Do not write final learner-facing content — this is a design specification.
- Use the CDD as the single source of truth for: module purpose, lesson sequence, lesson objectives, pacing, assessment placement, narrative flow.
- Ensure all design reflects a skill + practice-centered instructional approach: learners should DO, APPLY, and PRACTICE — not just consume content.
- Ensure logical progression across lessons and strong alignment within the module.

Teacher Mode: {{teacher_mode}}
Student Mode: {{student_mode}}

CRITICAL DESIGN LAYER:
Every lesson must include:
- A clear measurable objective
- Key concepts
- Practice opportunity
- Exploration opportunities
- Interaction opportunities (practice-oriented)
- Lesson Assessment

Module-level components must assess module-level learning only.

OUTPUT HIERARCHY:
Module
- Lesson
- Lesson
- Lesson
- Module-Level Components (Activity, Knowledge Check, Module Assessment)

--- USER ---
Create a detailed module blueprint based on the CDD context below.

**CDD Context (use this as the master reference — derive all module and lesson details from it):**
{{cdd_context}}

**Selected Module for Blueprint Generation:** {{selected_module}}

**Style Guidelines:** {{style_guidelines}}

{{extra_instructions}}

Work through each step fully before moving to the next.
Use ## for each section heading.

---

## Step 1 Module Identification and CDD Alignment
- Identify the selected module exactly as defined in the CDD
- Restate: Module title, Module duration, Module purpose, Number of lessons
- Confirm this blueprint is ONLY for the selected module
- Explain module placement in course arc: what comes before / what this module accomplishes / what it prepares for next
- List all lessons in exact CDD sequence
- Do NOT modify lesson structure

---

## Step 2 Module Blueprint Overview
Provide a high-level blueprint:
- Module goal (1–2 lines)
- Skill focus of the module
- Bloom's progression across lessons
- Narrative / instructional arc (intro → build → apply)
- Total module duration
- Lesson count
- Duration mapping: Lessons + Internal lesson sections + Module-level components

---

## Step 3 Lesson-by-Lesson Blueprint (Skill + Practice Focused)

For EACH lesson:

### Lesson Identification
- Lesson number, title, duration
- Connection to previous/next lesson

### Alignment Anchors
- Learning Objective (ONE line: verb + observable outcome)
- Design Intent (why this lesson exists at this point)
- Skill Focus (what learner practices or performs)
- Strategy Alignment: strategy and mechanism

### Key Concepts
- 3–5 concise concepts essential to the lesson

### Interaction Design (MANDATORY)
- Primary interaction (practice-based)
- Description of learner action

### Lesson Structure
Break lesson into topics (minimum 4 unless justified):
For each topic: Title, Duration, Format, Purpose, Concepts, Reinforcement Learning Components

---

## Step 4 Module-Level Components (GENERATE ONLY AFTER ALL LESSONS)

### Activity (End of Module)
- Objective, Type, Instructions, Output

### Knowledge Check
- Type, Coverage, Purpose

### Module Assessment
- Type, Focus, Lessons covered, Task structure, Learner deliverable, Evaluation focus

RULES: These components must assess MODULE-level learning, align to all lessons collectively, fit within module duration.

---

## Step 5 Blueprint Validation
Confirm:
- CDD used as source of truth
- Only selected module expanded
- All lessons included (no additions/removals)
- Lesson sequence unchanged
- Each lesson includes: objective, key concepts, interaction
- Skill + practice-centered design is consistent
- Topic durations align with lesson duration
- Total module timing is valid
- Module-level components placed AFTER lessons and aligned to module outcomes

---

## OUTPUT FORMAT (STRICT)

CONTENT TYPE: BLUEPRINT

## Module [Number]: [Title]

### Module Blueprint Details
Module Goal:
Skill Focus of the Module:
Bloom's Progression Across Lessons:
Progression Pattern:
Narrative/Instructional Arc:

### Module Structure

#### Lesson [Number]: [Lesson Title]
Lesson Details
- Lesson Number:
- Lesson Title:
- Lesson Duration:

Alignment Anchors
- Learning Objective:
- Design Intent:
- Skill Focus:
- Primary Strategy:
- Mechanism:

Key Concepts: 1. 2. 3. 4. 5.

Lesson Structure (Topics)
- Topic:
  - Duration:
  - Concepts:
  - Reinforcement Learning Components:

Lesson Assessment:
- Duration:
- Type:
- Focus:

Interaction Design
- Primary Interaction:
- Description of Learner Action:

[Repeat for all lessons]

### Module Assessment
- Title:
- Duration:
- Type:
- Alignment:
- Assessment Structure:
- Description:
- Success Criteria:

---

FINAL RULES:
- Do NOT generate lesson content
- Do NOT modify CDD structure
- Do NOT skip steps
- Keep output structured and concise
- Ensure strong skill + practice alignment
