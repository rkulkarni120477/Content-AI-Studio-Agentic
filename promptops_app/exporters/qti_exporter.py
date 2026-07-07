"""QTI 1.2 assessment XML builder for IMS Common Cartridge export.

Produces assessment XML compatible with Canvas, Moodle, and Blackboard imports.
Resource type in manifest: ``imsqti_xmlv1p2/imscc_xmlv1p1/assessment``
"""

from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from promptops_app.exporters.qti_parser import ParsedQuestion, parse_assessment_questions

_QTI_NS = "http://www.imsglobal.org/xsd/ims_qtiasiv1p2"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# IMS CC 1.1 assessment resource type (Canvas / Moodle / Blackboard)
ASSESSMENT_RESOURCE_TYPE = "imsqti_xmlv1p2/imscc_xmlv1p1/assessment"


def build_qti_assessment_xml(title: str, questions: list[ParsedQuestion]) -> str:
    """Build a complete QTI 1.2 assessment XML document."""
    safe_title = xml_escape(title or "Assessment")
    ident = _safe_ident(title or "assessment")
    items_xml = "\n".join(_render_item(q) for q in questions)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="{_QTI_NS}"
  xmlns:xsi="{_XSI_NS}"
  xsi:schemaLocation="{_QTI_NS} {_QTI_NS}p1.xsd">
  <assessment ident="{ident}" title="{safe_title}">
    <qtimetadata>
      <qtimetadatafield>
        <fieldlabel>cc_maxattempts</fieldlabel>
        <fieldentry>1</fieldentry>
      </qtimetadatafield>
    </qtimetadata>
    <section ident="root_section">
{items_xml}
    </section>
  </assessment>
</questestinterop>
"""


def build_qti_from_content(title: str, content: str) -> tuple[str, int]:
    """Parse content and return (qti_xml, question_count)."""
    questions = parse_assessment_questions(content)
    if not questions:
        return "", 0
    return build_qti_assessment_xml(title, questions), len(questions)


def _safe_ident(value: str) -> str:
    ident = "".join(c if c.isalnum() else "_" for c in value.lower())
    return ident[:60] or "assessment"


def _mattext(text: str) -> str:
    safe = xml_escape(text or "")
    return f'<mattext texttype="text/html">{safe}</mattext>'


def _feedback_block(text: str) -> str:
    if not text.strip():
        return ""
    return f"""        <itemfeedback ident="general_fb">
          <flow_mat>
            <material>{_mattext(text)}</material>
          </flow_mat>
        </itemfeedback>"""


def _render_item(q: ParsedQuestion) -> str:
    if q.qtype == "true_false_question":
        return _render_true_false(q)
    if q.qtype == "essay_question":
        return _render_essay(q)
    return _render_multiple_choice(q)


def _render_multiple_choice(q: ParsedQuestion) -> str:
    labels = "\n".join(
        f"""          <response_label ident="{xml_escape(ident)}">
            <material>{_mattext(text)}</material>
          </response_label>"""
        for ident, text in q.choices
    )
    correct = xml_escape(q.correct or (q.choices[0][0] if q.choices else "a"))
    fb = _feedback_block(q.feedback)

    return f"""      <item ident="{xml_escape(q.ident)}" title="{xml_escape(q.title)}">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>multiple_choice_question</fieldentry>
            </qtimetadatafield>
            <qtimetadatafield>
              <fieldlabel>points_possible</fieldlabel>
              <fieldentry>1</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material>{_mattext(q.stem)}</material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
{labels}
            </render_choice>
          </response_lid>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
          <respcondition continue="No">
            <conditionvar>
              <varequal respident="response1">{correct}</varequal>
            </conditionvar>
            <setvar action="Set" varname="SCORE">100</setvar>
          </respcondition>
        </resprocessing>
{fb}
      </item>"""


def _render_true_false(q: ParsedQuestion) -> str:
    correct = xml_escape(q.correct or "true")
    fb = _feedback_block(q.feedback)
    return f"""      <item ident="{xml_escape(q.ident)}" title="{xml_escape(q.title)}">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>true_false_question</fieldentry>
            </qtimetadatafield>
            <qtimetadatafield>
              <fieldlabel>points_possible</fieldlabel>
              <fieldentry>1</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material>{_mattext(q.stem)}</material>
          <response_lid ident="response1" rcardinality="Single">
            <render_choice>
              <response_label ident="true">
                <material>{_mattext("True")}</material>
              </response_label>
              <response_label ident="false">
                <material>{_mattext("False")}</material>
              </response_label>
            </render_choice>
          </response_lid>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
          <respcondition continue="No">
            <conditionvar>
              <varequal respident="response1">{correct}</varequal>
            </conditionvar>
            <setvar action="Set" varname="SCORE">100</setvar>
          </respcondition>
        </resprocessing>
{fb}
      </item>"""


def _render_essay(q: ParsedQuestion) -> str:
    fb = _feedback_block(q.feedback)
    return f"""      <item ident="{xml_escape(q.ident)}" title="{xml_escape(q.title)}">
        <itemmetadata>
          <qtimetadata>
            <qtimetadatafield>
              <fieldlabel>question_type</fieldlabel>
              <fieldentry>essay_question</fieldentry>
            </qtimetadatafield>
            <qtimetadatafield>
              <fieldlabel>points_possible</fieldlabel>
              <fieldentry>1</fieldentry>
            </qtimetadatafield>
          </qtimetadata>
        </itemmetadata>
        <presentation>
          <material>{_mattext(q.stem)}</material>
          <response_str ident="response1" rcardinality="Single">
            <render_fib>
              <response_label ident="answer1" rshuffle="No"/>
            </render_fib>
          </response_str>
        </presentation>
        <resprocessing>
          <outcomes>
            <decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/>
          </outcomes>
        </resprocessing>
{fb}
      </item>"""
