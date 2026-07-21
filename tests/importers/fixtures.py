"""Helpers to build in-memory IMSCC packages for importer tests."""

from __future__ import annotations

import zipfile
from io import BytesIO

_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="manifest_1"
  xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
  xmlns:imsmd="http://www.imsglobal.org/xsd/imsmd_v1p2">
  <metadata>
    <schema>IMS Common Cartridge</schema>
    <schemaversion>1.1.0</schemaversion>
    <imsmd:lom><imsmd:general><imsmd:title>
      <imsmd:langstring xml:lang="en">Sample Course</imsmd:langstring>
    </imsmd:title></imsmd:general></imsmd:lom>
  </metadata>
  <organizations>
    <organization identifier="org_1" structure="rooted-hierarchy">
      <item identifier="root_1">
        <item identifier="m1">
          <title>Module A</title>
          <item identifier="i_page1" identifierref="res_page1"><title>Intro</title></item>
          <item identifier="i_quiz1" identifierref="res_quiz1"><title>Quiz 1</title></item>
        </item>
        <item identifier="m2">
          <title>Module B</title>
          <item identifier="i_page2" identifierref="res_page2"><title>Wrap Up</title></item>
        </item>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="canvas_1" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" href="course_settings/canvas_export.txt"><file href="course_settings/canvas_export.txt"/></resource>
    <resource identifier="modmeta_1" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" href="course_settings/module_meta.xml"><file href="course_settings/module_meta.xml"/></resource>
    <resource identifier="res_page1" type="webcontent" href="wiki_content/page1.html"><file href="wiki_content/page1.html"/></resource>
    <resource identifier="res_quiz1" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment" href="assessment/quiz1.xml"><file href="assessment/quiz1.xml"/></resource>
    <resource identifier="res_page2" type="webcontent" href="wiki_content/page2.html"><file href="wiki_content/page2.html"/></resource>
  </resources>
</manifest>
"""

# Module B is listed BEFORE Module A in the file but has a higher <position>,
# so a correct parser must order by <position> (A then B). Module A also carries
# an unsupported external-tool item and an item pointing at a missing resource —
# both must degrade to warnings, not failures.
_MODULE_META = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <module identifier="m2">
    <title>Module B</title>
    <position>2</position>
    <items>
      <item identifier="i_page2">
        <content_type>WikiPage</content_type>
        <title>Wrap Up</title>
        <identifierref>res_page2</identifierref>
        <position>1</position>
      </item>
    </items>
  </module>
  <module identifier="m1">
    <title>Module A</title>
    <position>1</position>
    <items>
      <item identifier="i_page1">
        <content_type>WikiPage</content_type>
        <title>Intro</title>
        <identifierref>res_page1</identifierref>
        <position>1</position>
      </item>
      <item identifier="i_quiz1">
        <content_type>Quizzes::Quiz</content_type>
        <title>Quiz 1</title>
        <identifierref>res_quiz1</identifierref>
        <position>2</position>
      </item>
      <item identifier="i_lti">
        <content_type>ContextExternalTool</content_type>
        <title>External Tool</title>
        <identifierref>res_lti</identifierref>
        <position>3</position>
      </item>
      <item identifier="i_missing">
        <content_type>WikiPage</content_type>
        <title>Ghost</title>
        <identifierref>res_missing</identifierref>
        <position>4</position>
      </item>
    </items>
  </module>
</modules>
"""


def build_sample_imscc(*, with_module_meta: bool = True) -> bytes:
    """A small but realistic Canvas-style IMSCC package as raw zip bytes."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", _MANIFEST)
        zf.writestr("course_settings/canvas_export.txt", "This is a Canvas export.\n")
        if with_module_meta:
            zf.writestr("course_settings/module_meta.xml", _MODULE_META)
        zf.writestr("wiki_content/page1.html", "<html><head><title>Intro</title></head><body><p>Hello</p></body></html>")
        zf.writestr("wiki_content/page2.html", "<html><head><title>Wrap Up</title></head><body><p>Bye</p></body></html>")
        zf.writestr("assessment/quiz1.xml", "<questestinterop></questestinterop>")
        zf.writestr("web_resources/logo.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


# A wiki page in the exact shape the exporter emits: full HTML document with a
# locked <style> block and a .cas-lesson wrapper (with a nested .content div).
CAS_WIKI_PAGE = """<html>
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>Getting Started</title>
<meta name="identifier" content="res_page1"/>
<meta name="workflow_state" content="active"/>
</head>
<body>
<style>
.cas-lesson { font-family: Inter, sans-serif; line-height: 1.75; }
.cas-lesson h1 { color: #1e40af; }
</style>
<div class="cas-lesson">
  <h1>Getting Started</h1>
  <div class="content">
    <p>Welcome to the course. This lesson covers <strong>the basics</strong>.</p>
    <ul>
      <li>First point</li>
      <li>Second point</li>
    </ul>
  </div>
</div>
</body>
</html>"""


def sample_qti_xml() -> str:
    """A real QTI 1.2 assessment produced by the forward exporter (round-trip)."""
    from promptops_app.exporters.qti_exporter import build_qti_assessment_xml
    from promptops_app.exporters.qti_parser import ParsedQuestion

    questions = [
        ParsedQuestion(
            ident="question_1",
            title="Question 1",
            qtype="multiple_choice_question",
            stem="What is 2 + 2?",
            choices=[("a", "3"), ("b", "4"), ("c", "5"), ("d", "6")],
            correct="b",
            feedback="Basic arithmetic.",
        ),
        ParsedQuestion(
            ident="question_2",
            title="Question 2",
            qtype="true_false_question",
            stem="The sky is blue.",
            choices=[("true", "True"), ("false", "False")],
            correct="true",
            feedback="",
        ),
        ParsedQuestion(
            ident="question_3",
            title="Question 3",
            qtype="essay_question",
            stem="Explain the water cycle.",
            choices=[],
            correct="",
            feedback="Look for evaporation and condensation.",
        ),
    ]
    return build_qti_assessment_xml("Quiz 1", questions)


def build_content_imscc() -> bytes:
    """Package with real page bodies + a real QTI quiz, for the full-parse test."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", _MANIFEST)
        zf.writestr("course_settings/canvas_export.txt", "This is a Canvas export.\n")
        zf.writestr("course_settings/module_meta.xml", _MODULE_META)
        zf.writestr("wiki_content/page1.html", CAS_WIKI_PAGE)
        zf.writestr(
            "wiki_content/page2.html",
            "<html><head><title>Wrap Up</title></head><body>"
            '<div class="cas-lesson"><h1>Wrap Up</h1><p>Thanks for learning.</p></div>'
            "</body></html>",
        )
        zf.writestr("assessment/quiz1.xml", sample_qti_xml())
        zf.writestr("web_resources/logo.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


# A REAL-Canvas-shaped export (differs from the CAS self-export): lomimscc title,
# resource hrefs on <file> children, an assignment under the
# learning-application-resource type, and a quiz whose CC shell is empty with the
# real questions in non_cc_assessments/<id>.xml.qti. Regression for those fixes.
_REAL_CANVAS_MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="m"
  xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
  xmlns:lomimscc="http://ltsc.ieee.org/xsd/imscc/LOM">
  <metadata>
    <schema>IMS Common Cartridge</schema>
    <schemaversion>1.1.0</schemaversion>
    <lomimscc:lom><lomimscc:general><lomimscc:title>
      <lomimscc:string>Physics 101</lomimscc:string>
    </lomimscc:title></lomimscc:general></lomimscc:lom>
  </metadata>
  <organizations><organization identifier="o" structure="rooted-hierarchy"><item identifier="r"/></organization></organizations>
  <resources>
    <resource identifier="marker" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" href="course_settings/canvas_export.txt">
      <file href="course_settings/module_meta.xml"/>
    </resource>
    <resource identifier="rpage" type="webcontent" href="wiki_content/intro-0.html"><file href="wiki_content/intro-0.html"/></resource>
    <resource identifier="rassign" type="associatedcontent/imscc_xmlv1p1/learning-application-resource" href="gassign/electric.html"><file href="gassign/electric.html"/></resource>
    <resource identifier="rquiz" type="imsqti_xmlv1p2/imscc_xmlv1p1/assessment">
      <file href="rquiz/assessment_qti.xml"/>
      <dependency identifierref="dep"/>
    </resource>
  </resources>
</manifest>
"""

_REAL_CANVAS_MODULE_META = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="m1"><title>Module 1</title><position>1</position><items>
    <item identifier="i1"><content_type>WikiPage</content_type><title>Intro</title><identifierref>rpage</identifierref><position>1</position></item>
    <item identifier="i2"><content_type>Assignment</content_type><title>Electric current</title><identifierref>rassign</identifierref><position>2</position></item>
    <item identifier="i3"><content_type>Quizzes::Quiz</content_type><title>Resistance Quiz</title><identifierref>rquiz</identifierref><position>3</position></item>
  </items></module>
</modules>
"""

# CC quiz shell — empty section (questions come from the bank below).
_REAL_CANVAS_QTI_SHELL = """<?xml version="1.0"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <assessment ident="rquiz" title="Physics Quiz 1"><section ident="root_section"/></assessment>
</questestinterop>
"""

# non-CC question bank — the real questions (Canvas objectbank shape).
_REAL_CANVAS_NON_CC_QTI = """<?xml version="1.0" encoding="UTF-8"?>
<questestinterop xmlns="http://www.imsglobal.org/xsd/ims_qtiasiv1p2">
  <objectbank ident="rquiz">
    <item ident="q1" title="Question">
      <itemmetadata><qtimetadata><qtimetadatafield>
        <fieldlabel>question_type</fieldlabel><fieldentry>multiple_choice_question</fieldentry>
      </qtimetadatafield></qtimetadata></itemmetadata>
      <presentation>
        <material><mattext texttype="text/html">&lt;p&gt;Unit of resistance?&lt;/p&gt;</mattext></material>
        <response_lid ident="response1" rcardinality="Single"><render_choice>
          <response_label ident="1"><material><mattext>ohm</mattext></material></response_label>
          <response_label ident="2"><material><mattext>second</mattext></material></response_label>
        </render_choice></response_lid>
      </presentation>
      <resprocessing><outcomes><decvar maxvalue="100" minvalue="0" varname="SCORE" vartype="Decimal"/></outcomes>
        <respcondition continue="No"><conditionvar><varequal respident="response1">1</varequal></conditionvar></respcondition>
      </resprocessing>
    </item>
  </objectbank>
</questestinterop>
"""


def build_real_canvas_imscc() -> bytes:
    """A real-Canvas-shaped export exercising the file-child href / assignment /
    lomimscc-title / non-CC-quiz-bank handling."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", _REAL_CANVAS_MANIFEST)
        zf.writestr("course_settings/canvas_export.txt", "Canvas export.\n")
        zf.writestr("course_settings/module_meta.xml", _REAL_CANVAS_MODULE_META)
        zf.writestr("course_settings/course_settings.xml", "<course><title>Physics 101</title></course>")
        zf.writestr("wiki_content/intro-0.html", "<html><head><title>Intro</title></head><body><p>Welcome to physics.</p></body></html>")
        zf.writestr("gassign/electric.html", "<html><head><title>Electric current</title></head><body><p>Assignment about electric current.</p></body></html>")
        zf.writestr("rquiz/assessment_qti.xml", _REAL_CANVAS_QTI_SHELL)
        zf.writestr("non_cc_assessments/rquiz.xml.qti", _REAL_CANVAS_NON_CC_QTI)
    return buf.getvalue()


def build_imscc_with_orphans() -> bytes:
    """One page in a module + two pages NOT in any module (one unique, one an exact
    duplicate of the module page). Exercises orphan recovery + dedup."""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="m" xmlns="http://www.imsglobal.org/xsd/imscp_v1p1"
  xmlns:lomimscc="http://ltsc.ieee.org/xsd/imscc/LOM">
  <metadata><schema>IMS Common Cartridge</schema><schemaversion>1.1.0</schemaversion>
    <lomimscc:lom><lomimscc:general><lomimscc:title>
      <lomimscc:string>Orphan Course</lomimscc:string>
    </lomimscc:title></lomimscc:general></lomimscc:lom>
  </metadata>
  <organizations><organization identifier="o" structure="rooted-hierarchy"><item identifier="r"/></organization></organizations>
  <resources>
    <resource identifier="rp" type="webcontent" href="wiki_content/in-module-0.html"><file href="wiki_content/in-module-0.html"/></resource>
    <resource identifier="ro" type="webcontent" href="wiki_content/orphan-1.html"><file href="wiki_content/orphan-1.html"/></resource>
    <resource identifier="rd" type="webcontent" href="wiki_content/dup-2.html"><file href="wiki_content/dup-2.html"/></resource>
  </resources>
</manifest>
"""
    module_meta = """<?xml version="1.0" encoding="UTF-8"?>
<modules xmlns="http://canvas.instructure.com/xsd/cccv1p0">
  <module identifier="m1"><title>Module 1</title><position>1</position><items>
    <item identifier="i1"><content_type>WikiPage</content_type><title>In Module</title><identifierref>rp</identifierref><position>1</position></item>
  </items></module>
</modules>
"""
    body_in = "<html><head><title>In Module</title></head><body><p>Module page content here.</p></body></html>"
    body_orphan = "<html><head><title>Orphan Page</title></head><body><p>Unique orphan content only here.</p></body></html>"
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", manifest)
        zf.writestr("course_settings/module_meta.xml", module_meta)
        zf.writestr("wiki_content/in-module-0.html", body_in)
        zf.writestr("wiki_content/orphan-1.html", body_orphan)
        zf.writestr("wiki_content/dup-2.html", body_in)   # exact duplicate of the module page
    return buf.getvalue()


def build_zip_without_manifest() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("readme.txt", "no manifest here")
    return buf.getvalue()


def build_zip_with_traversal() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", _MANIFEST)
        zf.writestr("../evil.txt", "escape attempt")
    return buf.getvalue()


# ── CendocXML fixtures ──────────────────────────────────────────────────────

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)

_CENDOC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<cl:doc identifier="DOC1" xmlns:cl="http://xml.cengage-learning.com/cendoc-core"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <cl:doc-meta identifier="META1">
    <cl:title identifier="T0">Sample Cendoc Book</cl:title>
  </cl:doc-meta>
  <cl:front-matter identifier="FM1">
    <cl:dedication identifier="D1"><cl:para identifier="P0">Dedicated to testers.</cl:para></cl:dedication>
  </cl:front-matter>
  <cl:body-matter identifier="BM1">
    <cl:part identifier="PART1">
      <cl:complex-meta><cl:title identifier="PT1">Part One</cl:title></cl:complex-meta>
      <cl:chapter identifier="CH1">
        <cl:complex-meta>
          <cl:title identifier="CT1">An Overview</cl:title>
          <cl:label>Chapter <cl:ordinal>1</cl:ordinal></cl:label>
        </cl:complex-meta>
        <cl:opener identifier="OP1">
          <cl:sect1 number="nonumber" identifier="OPSECT">
            <cl:figure identifier="FIG0" number="nonumber">
              <cl:simple-meta><cl:alt-text identifier="A0">cover</cl:alt-text></cl:simple-meta>
              <cl:media identifier="M0">
                <cl:media-object identifier="MO0" link-target="cover.png" media-size="page"/>
              </cl:media>
            </cl:figure>
            <cl:sidebar identifier="SB0">
              <cl:complex-meta><cl:label>Learning Outcomes</cl:label></cl:complex-meta>
              <cl:list identifier="LO1" list-style="Customized">
                <cl:item identifier="LI1" manual-label="1-1">
                  <cl:para identifier="LP1">Define marketing</cl:para>
                </cl:item>
              </cl:list>
            </cl:sidebar>
          </cl:sect1>
        </cl:opener>
        <cl:sect1 identifier="S1">
          <cl:complex-meta>
            <cl:title identifier="ST1">What Is Marketing?</cl:title>
            <cl:label><cl:ordinal>1-1</cl:ordinal></cl:label>
          </cl:complex-meta>
          <cl:para identifier="P1">Marketing means <cl:style identifier="STY1" styles="italic">value</cl:style> for customers.</cl:para>
          <cl:figure identifier="FIG1" number="nonumber">
            <cl:simple-meta>
              <cl:caption identifier="CAP1"><cl:para identifier="CP1">A sample figure.</cl:para></cl:caption>
              <cl:alt-text identifier="A1">sample</cl:alt-text>
            </cl:simple-meta>
            <cl:media identifier="M1">
              <cl:media-object identifier="MO1" link-target="fig1.png" media-size="page" width="10" height="10"/>
              <cl:media-object identifier="MO2" link-target="fig1-full.png" media-size="full"/>
            </cl:media>
          </cl:figure>
          <cl:sect2 identifier="S2A">
            <cl:complex-meta><cl:title identifier="ST2A">A Nested Topic</cl:title></cl:complex-meta>
            <cl:para identifier="P2">Nested section body.</cl:para>
          </cl:sect2>
        </cl:sect1>
        <cl:sect1 identifier="S2">
          <cl:complex-meta>
            <cl:title identifier="ST2">Why Study Marketing?</cl:title>
          </cl:complex-meta>
          <cl:para identifier="P3">Because it matters.</cl:para>
          <cl:list identifier="L1" list-style="Ordered" numeration="arabic">
            <cl:item identifier="I1"><cl:para identifier="IP1">Reason one</cl:para></cl:item>
            <cl:item identifier="I2"><cl:para identifier="IP2">Reason two</cl:para></cl:item>
          </cl:list>
        </cl:sect1>
      </cl:chapter>
    </cl:part>
  </cl:body-matter>
  <cl:back-matter identifier="BACK1">
    <cl:appendix identifier="APP1">
      <cl:simple-section number="nonumber" identifier="APS1">
        <cl:complex-meta><cl:title identifier="APT1">AI Appendix</cl:title></cl:complex-meta>
        <cl:para identifier="AP1">Appendix prose.</cl:para>
        <cl:quiz identifier="QZ1">
          <cl:metadata-wrapper>
            <cl:descriptive-meta><cl:broad-term>Chapter exercises</cl:broad-term></cl:descriptive-meta>
          </cl:metadata-wrapper>
          <cl:question-section identifier="QS1">
            <cl:short-answer-section identifier="SA1">
              <cl:sa-item identifier="SAI1">
                <cl:question identifier="QQ1"><cl:para identifier="QP1">What is AI?</cl:para></cl:question>
                <cl:answer identifier="QA1"><cl:para identifier="QAP1">A set of tools.</cl:para></cl:answer>
              </cl:sa-item>
            </cl:short-answer-section>
          </cl:question-section>
        </cl:quiz>
      </cl:simple-section>
    </cl:appendix>
  </cl:back-matter>
</cl:doc>
"""


def build_sample_cendoc(*, with_images: bool = True, missing_image: bool = False) -> bytes:
    """Tiny CendocXML zip: 1 chapter, 2 sect1s, figure, quiz, optional book_images."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("cendoc-sample.xml", _CENDOC_XML)
        if with_images:
            zf.writestr("book_images/cover.png", _TINY_PNG)
            if not missing_image:
                zf.writestr("book_images/fig1.png", _TINY_PNG)
            zf.writestr("book_images/fig1-full.png", _TINY_PNG)
    return buf.getvalue()


def build_ambiguous_imscc_and_cendoc() -> bytes:
    """Zip containing both imsmanifest.xml and a Cendoc XML — IMSCC must win."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", _MANIFEST)
        zf.writestr("course_settings/canvas_export.txt", "Canvas\n")
        zf.writestr("cendoc-sample.xml", _CENDOC_XML)
    return buf.getvalue()
