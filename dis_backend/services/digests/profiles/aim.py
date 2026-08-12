"""AIM CurriculumProfile — profile #1 (plan §5.5 / D8).

AIM's curriculum is Block → **Day** (``dis_calendar_days``) with an **ACS-code**
coverage set (``dis_content_units.metadata_json.acs_codes``). This profile owns
every AIM-specific read; the shared ``enumerate_block`` assembler calls through it
and touches no AIM table directly.

The SQL here is lifted verbatim from the original Slice-A ``enumerate.py`` so AIM
enumeration output is byte-for-byte unchanged by the D8 refactor.
"""
from __future__ import annotations

from typing import Any, Dict, List

from services.digests.profiles.base import CurriculumProfile, ScopeData


class AIMCurriculumProfile(CurriculumProfile):
    coverage_label = "ACS"

    def load_scope(self, cur, schema: str, client_id: str, block: str) -> ScopeData:
        calendar_id, dup_ids, total_days = self._pick_calendar(cur, schema, client_id, block)
        days = self._load_days(cur, schema, calendar_id)
        units = self._load_units(cur, schema, client_id, block)
        return ScopeData(
            calendar_id=calendar_id,
            total_days=total_days,
            days=days,
            units=units,
            duplicate_calendar_ids=dup_ids,
        )

    def coverage_codes(self, unit: Dict[str, Any]) -> List[str]:
        codes = (unit.get("metadata_json") or {}).get("acs_codes") or []
        return [c for c in codes if c]

    # -- AIM reads (SELECT-only; caller's connection is read_only) ------------
    def _pick_calendar(self, cur, schema: str, client_id: str, block: str):
        """Canonical calendar = the one with the most day rows (tie-break newest).

        Phase 0 found 3 Block-2 calendars; the richest one is authoritative. Others
        are reported as DUPLICATE_CALENDAR, never silently dropped.
        """
        cur.execute(
            f"""SELECT c.calendar_id, c.total_days, c.created_at,
                       count(d.calendar_day_id) AS day_rows
                  FROM {schema}.dis_course_calendars c
                  LEFT JOIN {schema}.dis_calendar_days d ON d.calendar_id = c.calendar_id
                 WHERE c.client_id = %s AND c.block = %s
                 GROUP BY c.calendar_id, c.total_days, c.created_at
                 ORDER BY day_rows DESC, c.created_at DESC""",
            (client_id, block),
        )
        rows = cur.fetchall()
        if not rows:
            raise LookupError(
                f"No calendar found for client={client_id!r} block={block!r} in {schema}.dis_course_calendars"
            )
        canonical = rows[0]
        dup_ids = [r["calendar_id"] for r in rows[1:]]
        return canonical["calendar_id"], dup_ids, int(canonical.get("total_days") or 0)

    def _load_days(self, cur, schema: str, calendar_id: str) -> List[Dict[str, Any]]:
        cur.execute(
            f"""SELECT calendar_day_id, day_number, week_number, topic, lesson_title,
                       source_text,
                       activities_json::text  AS activities_json,
                       assignments_json::text AS assignments_json,
                       assessments_json::text AS assessments_json
                  FROM {schema}.dis_calendar_days
                 WHERE calendar_id = %s AND day_number IS NOT NULL
                 ORDER BY day_number""",
            (calendar_id,),
        )
        return cur.fetchall()

    def _load_units(self, cur, schema: str, client_id: str, block: str) -> List[Dict[str, Any]]:
        cur.execute(
            f"""SELECT content_unit_id, unit_type, title, text_content, content_hash,
                       metadata_json
                  FROM {schema}.dis_content_units
                 WHERE client_id = %s AND metadata_json->>'block' = %s""",
            (client_id, block),
        )
        return cur.fetchall()
