--
-- PostgreSQL database dump
--


-- Dumped from database version 17.10 (Debian 17.10-1.pgdg13+1)
-- Dumped by pg_dump version 17.10 (Ubuntu 17.10-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ab_test_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ab_test_runs (
    id integer NOT NULL,
    prompt_name character varying(150) NOT NULL,
    variant_a character varying(50) NOT NULL,
    variant_b character varying(50) NOT NULL,
    topic character varying(255) NOT NULL,
    output_a text NOT NULL,
    output_b text NOT NULL,
    created_by character varying(100),
    created_at timestamp without time zone
);


--
-- Name: ab_test_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ab_test_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ab_test_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ab_test_runs_id_seq OWNED BY public.ab_test_runs.id;


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id integer NOT NULL,
    user_id character varying(100) NOT NULL,
    action character varying(120) NOT NULL,
    entity_type character varying(60),
    entity_id character varying(64),
    project_id integer,
    course_id integer,
    metadata_json text,
    ip_address character varying(45),
    created_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_logs_id_seq OWNED BY public.audit_logs.id;


--
-- Name: block_comments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.block_comments (
    id integer NOT NULL,
    block_id integer NOT NULL,
    comment text NOT NULL,
    author character varying(100),
    created_at timestamp without time zone
);


--
-- Name: block_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.block_comments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: block_comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.block_comments_id_seq OWNED BY public.block_comments.id;


--
-- Name: block_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.block_versions (
    id integer NOT NULL,
    block_id integer NOT NULL,
    version_num integer NOT NULL,
    content text NOT NULL,
    change_source character varying(50),
    change_note text,
    workflow_state_at_save character varying(50),
    word_count integer,
    created_by character varying(100),
    created_at timestamp without time zone
);


--
-- Name: block_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.block_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: block_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.block_versions_id_seq OWNED BY public.block_versions.id;


--
-- Name: blocks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blocks (
    id integer NOT NULL,
    generation_id integer,
    block_type character varying,
    block_label character varying,
    content text,
    sources text,
    plagiarism_score integer,
    plagiarism_report text,
    eval_score integer,
    eval_report text,
    ai_review text,
    workflow_state character varying,
    rating integer,
    reviewer_comment text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    version_num integer DEFAULT 1,
    assigned_reviewer character varying(100),
    review_requested_at timestamp without time zone,
    approved_by character varying(100),
    approved_at timestamp without time zone,
    rejected_reason text,
    draft_content text,
    draft_saved_at timestamp without time zone,
    draft_saved_by character varying(100),
    submitted_by character varying(100),
    reviewed_by character varying(100),
    reviewed_at timestamp without time zone,
    review_comments text,
    archived_by character varying(100),
    archived_at timestamp without time zone,
    "position" integer DEFAULT 0
);


--
-- Name: blocks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.blocks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: blocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.blocks_id_seq OWNED BY public.blocks.id;


--
-- Name: blueprint_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blueprint_versions (
    id integer NOT NULL,
    blueprint_id integer,
    version character varying(20) NOT NULL,
    full_content text,
    sections text,
    generation_params text,
    change_reason text,
    is_active boolean,
    created_by character varying(100),
    created_at timestamp without time zone,
    version_number integer,
    parent_version_id integer
);


--
-- Name: blueprint_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.blueprint_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: blueprint_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.blueprint_versions_id_seq OWNED BY public.blueprint_versions.id;


--
-- Name: cdd_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cdd_versions (
    id integer NOT NULL,
    cdd_id integer,
    version character varying(20) NOT NULL,
    full_content text,
    sections text,
    generation_params text,
    change_reason text,
    is_active boolean,
    created_by character varying(100),
    created_at timestamp without time zone,
    version_number integer,
    parent_version_id integer
);


--
-- Name: cdd_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cdd_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cdd_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cdd_versions_id_seq OWNED BY public.cdd_versions.id;


--
-- Name: central_repositories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.central_repositories (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    item_type character varying(50) NOT NULL,
    content text NOT NULL,
    description text,
    source_module character varying(100),
    project_id integer,
    cluster_id integer,
    course_id integer,
    client_name character varying(255),
    cluster_name character varying(255),
    tags character varying(500),
    status character varying(20),
    usage_count integer,
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    last_used_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: central_repositories_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.central_repositories_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: central_repositories_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.central_repositories_id_seq OWNED BY public.central_repositories.id;


--
-- Name: cluster_prompts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cluster_prompts (
    id integer NOT NULL,
    cluster_id integer,
    name character varying(255) NOT NULL,
    description text,
    system_prompt text,
    user_prompt_template text,
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    is_active boolean
);


--
-- Name: cluster_prompts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.cluster_prompts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cluster_prompts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.cluster_prompts_id_seq OWNED BY public.cluster_prompts.id;


--
-- Name: clusters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clusters (
    id integer NOT NULL,
    project_id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_by character varying(100),
    created_at timestamp without time zone,
    is_active boolean
);


--
-- Name: clusters_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.clusters_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: clusters_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.clusters_id_seq OWNED BY public.clusters.id;


--
-- Name: course_design_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.course_design_documents (
    id integer NOT NULL,
    title character varying(255) NOT NULL,
    course_title character varying(255) NOT NULL,
    description text,
    active_version character varying(20),
    workflow_state character varying(50),
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    project_id integer,
    course_id integer
);


--
-- Name: course_design_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.course_design_documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: course_design_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.course_design_documents_id_seq OWNED BY public.course_design_documents.id;


--
-- Name: course_user_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.course_user_assignments (
    id integer NOT NULL,
    course_id integer NOT NULL,
    username character varying(100) NOT NULL,
    assigned_at timestamp without time zone
);


--
-- Name: course_user_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.course_user_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: course_user_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.course_user_assignments_id_seq OWNED BY public.course_user_assignments.id;


--
-- Name: courses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.courses (
    id integer NOT NULL,
    project_id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_by character varying(100),
    created_at timestamp without time zone,
    is_active boolean,
    active_style_id integer,
    active_cdd_id integer,
    cluster_id integer,
    active_blueprint_id integer,
    config_model_choice character varying(100),
    config_expert_domain character varying(255),
    config_target_audience character varying(255),
    config_audience_category character varying(100)
);


--
-- Name: courses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.courses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: courses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.courses_id_seq OWNED BY public.courses.id;


--
-- Name: document_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document_chunks (
    id integer NOT NULL,
    document_id integer NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    token_estimate integer,
    embedding_json text,
    created_at timestamp without time zone
);


--
-- Name: document_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.document_chunks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: document_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.document_chunks_id_seq OWNED BY public.document_chunks.id;


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    file_type character varying(120),
    doc_tag character varying(50),
    content text NOT NULL,
    uploaded_by character varying(100),
    uploaded_at timestamp without time zone,
    updated_at timestamp without time zone,
    status character varying(20),
    tenant_id character varying(36)
);


--
-- Name: documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.documents_id_seq OWNED BY public.documents.id;


--
-- Name: feedback_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.feedback_signals (
    id integer NOT NULL,
    block_id integer NOT NULL,
    generation_id integer,
    prompt_name character varying(150),
    block_type character varying(80),
    signal_source character varying(20) NOT NULL,
    feedback_scope character varying(20) NOT NULL,
    original_content text,
    final_content text,
    user_instruction text,
    edit_reason text,
    topic character varying(255),
    author character varying(100),
    created_at timestamp without time zone
);


--
-- Name: feedback_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.feedback_signals_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: feedback_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.feedback_signals_id_seq OWNED BY public.feedback_signals.id;


--
-- Name: generation_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generation_jobs (
    id character varying(64) NOT NULL,
    job_type character varying(50),
    status character varying(30),
    progress integer,
    current_step character varying(160),
    input_payload_json text,
    result_json text,
    result_entity_id integer,
    error_message text,
    project_id integer,
    course_id integer,
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    completed_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: generations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.generations (
    id integer NOT NULL,
    prompt_name character varying(150) NOT NULL,
    prompt_version character varying(50) NOT NULL,
    block_type character varying(80) NOT NULL,
    topic character varying(255) NOT NULL,
    output_text text NOT NULL,
    cdd_id integer,
    cdd_version character varying(20),
    blueprint_id integer,
    blueprint_version character varying(20),
    project_id integer,
    course_id integer,
    created_by character varying(100),
    created_at timestamp without time zone
);


--
-- Name: generations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.generations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: generations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.generations_id_seq OWNED BY public.generations.id;


--
-- Name: job_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_metrics (
    id integer NOT NULL,
    job_id character varying(64) NOT NULL,
    stage character varying(120) NOT NULL,
    started_at timestamp without time zone,
    ended_at timestamp without time zone,
    duration_ms integer,
    model_name character varying(160),
    input_chars integer,
    output_chars integer,
    status character varying(30),
    error_message text
);


--
-- Name: job_metrics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.job_metrics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: job_metrics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.job_metrics_id_seq OWNED BY public.job_metrics.id;


--
-- Name: llm_usage_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_usage_logs (
    id integer NOT NULL,
    user_id character varying(100),
    project_id integer,
    course_id integer,
    entity_type character varying(60),
    entity_id character varying(64),
    prompt_template character varying(150),
    prompt_version character varying(50),
    model_name character varying(160) NOT NULL,
    input_tokens integer,
    output_tokens integer,
    total_tokens integer,
    estimated_cost double precision,
    duration_ms integer,
    status character varying(30) NOT NULL,
    error_message text,
    created_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: llm_usage_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_usage_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_usage_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_usage_logs_id_seq OWNED BY public.llm_usage_logs.id;


--
-- Name: module_blueprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.module_blueprints (
    id integer NOT NULL,
    cdd_id integer,
    title character varying(255) NOT NULL,
    module_title character varying(255) NOT NULL,
    module_number integer,
    module_objective text,
    active_version character varying(20),
    workflow_state character varying(50),
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    project_id integer,
    course_id integer
);


--
-- Name: module_blueprints_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.module_blueprints_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: module_blueprints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.module_blueprints_id_seq OWNED BY public.module_blueprints.id;


--
-- Name: pl_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_attachments (
    id character varying(36) NOT NULL,
    prompt_id character varying(36) NOT NULL,
    original_name character varying(300),
    stored_name character varying(500),
    size_bytes bigint,
    uploaded_by character varying(100),
    uploaded_at timestamp with time zone
);


--
-- Name: pl_audit_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_audit_events (
    id character varying(36) NOT NULL,
    event_type character varying(64) NOT NULL,
    actor_username character varying(100),
    actor_role character varying(64),
    entity_type character varying(64),
    entity_id character varying(64),
    action character varying(32) NOT NULL,
    summary text NOT NULL,
    changes json,
    ip_address character varying(64),
    user_agent character varying(512),
    created_at timestamp with time zone NOT NULL
);


--
-- Name: pl_prompt_requests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompt_requests (
    id character varying(36) NOT NULL,
    title character varying(300) NOT NULL,
    description text,
    type character varying(20) NOT NULL,
    prompt_id character varying(36),
    requested_by character varying(100) NOT NULL,
    status character varying(20) NOT NULL,
    admin_notes text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


--
-- Name: pl_prompt_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompt_tags (
    prompt_id character varying(36) NOT NULL,
    tag character varying(100) NOT NULL
);


--
-- Name: pl_prompt_teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompt_teams (
    prompt_id character varying(36) NOT NULL,
    team_id character varying(20) NOT NULL
);


--
-- Name: pl_prompt_variables; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompt_variables (
    id integer NOT NULL,
    prompt_id character varying(36) NOT NULL,
    name character varying(100) NOT NULL,
    label character varying(200),
    hint text,
    sort_order integer NOT NULL
);


--
-- Name: pl_prompt_variables_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pl_prompt_variables_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pl_prompt_variables_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pl_prompt_variables_id_seq OWNED BY public.pl_prompt_variables.id;


--
-- Name: pl_prompt_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompt_versions (
    id character varying(36) NOT NULL,
    prompt_id character varying(36) NOT NULL,
    version_number integer NOT NULL,
    content text NOT NULL,
    note text,
    created_by character varying(100),
    created_at timestamp with time zone
);


--
-- Name: pl_prompts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_prompts (
    id character varying(36) NOT NULL,
    parent_id character varying(36),
    title character varying(300) NOT NULL,
    content text NOT NULL,
    description text,
    category character varying(100),
    visibility character varying(20) NOT NULL,
    team_id character varying(20),
    created_by character varying(100),
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    last_used_at timestamp with time zone,
    deleted_at timestamp with time zone
);


--
-- Name: pl_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_reviews (
    id character varying(36) NOT NULL,
    prompt_id character varying(36) NOT NULL,
    username character varying(100) NOT NULL,
    rating smallint NOT NULL,
    feedback text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


--
-- Name: pl_teams; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pl_teams (
    id character varying(20) NOT NULL,
    name character varying(100) NOT NULL,
    created_at timestamp with time zone,
    created_by character varying(100)
);


--
-- Name: plagiarism_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plagiarism_reports (
    id integer NOT NULL,
    block_id integer NOT NULL,
    project_id integer,
    course_id integer,
    scan_id character varying(64),
    celery_task_id character varying(155),
    status character varying(20) NOT NULL,
    similarity_score double precision,
    ai_score double precision,
    source_urls json,
    highlights json,
    raw_response text,
    error_message text,
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    submitted_at timestamp without time zone,
    completed_at timestamp without time zone
);


--
-- Name: plagiarism_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.plagiarism_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: plagiarism_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.plagiarism_reports_id_seq OWNED BY public.plagiarism_reports.id;


--
-- Name: project_user_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.project_user_assignments (
    id integer NOT NULL,
    project_id integer NOT NULL,
    username character varying(100) NOT NULL,
    assigned_at timestamp without time zone
);


--
-- Name: project_user_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.project_user_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: project_user_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.project_user_assignments_id_seq OWNED BY public.project_user_assignments.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    client_name character varying(255),
    created_by character varying(100),
    created_at timestamp without time zone,
    is_active boolean,
    active_style_id integer,
    tenant_id character varying(36)
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: prompt_fixings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompt_fixings (
    id integer NOT NULL,
    component character varying(50) NOT NULL,
    scope_level character varying(20) NOT NULL,
    project_id integer,
    cluster_id integer,
    course_id integer,
    prompt_id integer,
    fixed_by character varying(100) NOT NULL,
    fixed_by_role character varying(20) NOT NULL,
    fixed_at timestamp without time zone
);


--
-- Name: prompt_fixings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prompt_fixings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prompt_fixings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prompt_fixings_id_seq OWNED BY public.prompt_fixings.id;


--
-- Name: prompt_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompt_versions (
    id integer NOT NULL,
    prompt_id integer,
    version character varying,
    system_prompt text,
    user_prompt_template text,
    change_reason text,
    is_active boolean,
    created_at timestamp without time zone,
    created_by character varying(100)
);


--
-- Name: prompt_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prompt_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prompt_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prompt_versions_id_seq OWNED BY public.prompt_versions.id;


--
-- Name: prompts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.prompts (
    id integer NOT NULL,
    name character varying,
    description text,
    owner character varying,
    active_version character varying,
    tags character varying,
    created_at timestamp without time zone,
    component_type character varying(50),
    is_default boolean DEFAULT false,
    updated_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: prompts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.prompts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: prompts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.prompts_id_seq OWNED BY public.prompts.id;


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reviews (
    id integer NOT NULL,
    generation_id integer NOT NULL,
    block_id integer,
    reviewer character varying(100),
    reviewer_role character varying(50),
    score integer,
    approved boolean,
    comments text,
    created_at timestamp without time zone
);


--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reviews_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: style_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.style_documents (
    id integer NOT NULL,
    style_id integer NOT NULL,
    document_id integer NOT NULL
);


--
-- Name: style_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.style_documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: style_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.style_documents_id_seq OWNED BY public.style_documents.id;


--
-- Name: style_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.style_versions (
    id integer NOT NULL,
    style_id integer NOT NULL,
    version_number integer NOT NULL,
    is_active boolean,
    understanding_content text,
    change_summary character varying(500),
    created_by character varying(100),
    created_at timestamp without time zone
);


--
-- Name: style_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.style_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: style_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.style_versions_id_seq OWNED BY public.style_versions.id;


--
-- Name: styles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.styles (
    id integer NOT NULL,
    style_id character varying(120) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    custom_instructions text,
    generated_summary text,
    understanding_status character varying(20),
    is_active boolean,
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    tenant_id character varying(36)
);


--
-- Name: styles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.styles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: styles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.styles_id_seq OWNED BY public.styles.id;


--
-- Name: system_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_logs (
    id integer NOT NULL,
    event_type character varying(80) NOT NULL,
    actor character varying(100),
    details text,
    metadata_json text,
    created_at timestamp without time zone
);


--
-- Name: system_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.system_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: system_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.system_logs_id_seq OWNED BY public.system_logs.id;


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id character varying(36) NOT NULL,
    slug character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    max_users integer NOT NULL,
    status character varying(20) NOT NULL,
    created_at timestamp without time zone,
    created_by character varying(100)
);


--
-- Name: user_prompt_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_prompt_history (
    id integer NOT NULL,
    name character varying(255) NOT NULL,
    component character varying(50) NOT NULL,
    content text NOT NULL,
    version_number integer NOT NULL,
    project_id integer,
    cluster_id integer,
    course_id integer,
    created_by character varying(100),
    created_at timestamp without time zone,
    updated_at timestamp without time zone,
    is_active boolean
);


--
-- Name: user_prompt_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_prompt_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_prompt_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_prompt_history_id_seq OWNED BY public.user_prompt_history.id;


--
-- Name: user_prompt_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_prompt_preferences (
    id integer NOT NULL,
    user_name character varying(100) NOT NULL,
    component character varying(50) NOT NULL,
    course_id integer,
    project_id integer,
    prompt_id integer,
    updated_at timestamp without time zone
);


--
-- Name: user_prompt_preferences_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_prompt_preferences_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: user_prompt_preferences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_prompt_preferences_id_seq OWNED BY public.user_prompt_preferences.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying,
    password_hash character varying,
    role character varying,
    permissions text,
    is_active boolean,
    created_at timestamp without time zone,
    tenant_id character varying(36),
    is_platform_admin boolean DEFAULT false NOT NULL,
    project_id integer
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: workflow_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workflow_events (
    id integer NOT NULL,
    block_id integer NOT NULL,
    from_state character varying(50) NOT NULL,
    to_state character varying(50) NOT NULL,
    action character varying(80) NOT NULL,
    actor character varying(100),
    created_at timestamp without time zone,
    comment text DEFAULT ''::text
);


--
-- Name: workflow_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.workflow_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: workflow_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.workflow_events_id_seq OWNED BY public.workflow_events.id;


--
-- Name: ab_test_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_test_runs ALTER COLUMN id SET DEFAULT nextval('public.ab_test_runs_id_seq'::regclass);


--
-- Name: audit_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN id SET DEFAULT nextval('public.audit_logs_id_seq'::regclass);


--
-- Name: block_comments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_comments ALTER COLUMN id SET DEFAULT nextval('public.block_comments_id_seq'::regclass);


--
-- Name: block_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_versions ALTER COLUMN id SET DEFAULT nextval('public.block_versions_id_seq'::regclass);


--
-- Name: blocks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blocks ALTER COLUMN id SET DEFAULT nextval('public.blocks_id_seq'::regclass);


--
-- Name: blueprint_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprint_versions ALTER COLUMN id SET DEFAULT nextval('public.blueprint_versions_id_seq'::regclass);


--
-- Name: cdd_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdd_versions ALTER COLUMN id SET DEFAULT nextval('public.cdd_versions_id_seq'::regclass);


--
-- Name: central_repositories id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.central_repositories ALTER COLUMN id SET DEFAULT nextval('public.central_repositories_id_seq'::regclass);


--
-- Name: cluster_prompts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_prompts ALTER COLUMN id SET DEFAULT nextval('public.cluster_prompts_id_seq'::regclass);


--
-- Name: clusters id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clusters ALTER COLUMN id SET DEFAULT nextval('public.clusters_id_seq'::regclass);


--
-- Name: course_design_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.course_design_documents ALTER COLUMN id SET DEFAULT nextval('public.course_design_documents_id_seq'::regclass);


--
-- Name: course_user_assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.course_user_assignments ALTER COLUMN id SET DEFAULT nextval('public.course_user_assignments_id_seq'::regclass);


--
-- Name: courses id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses ALTER COLUMN id SET DEFAULT nextval('public.courses_id_seq'::regclass);


--
-- Name: document_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_chunks ALTER COLUMN id SET DEFAULT nextval('public.document_chunks_id_seq'::regclass);


--
-- Name: documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents ALTER COLUMN id SET DEFAULT nextval('public.documents_id_seq'::regclass);


--
-- Name: feedback_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feedback_signals ALTER COLUMN id SET DEFAULT nextval('public.feedback_signals_id_seq'::regclass);


--
-- Name: generations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generations ALTER COLUMN id SET DEFAULT nextval('public.generations_id_seq'::regclass);


--
-- Name: job_metrics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_metrics ALTER COLUMN id SET DEFAULT nextval('public.job_metrics_id_seq'::regclass);


--
-- Name: llm_usage_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage_logs ALTER COLUMN id SET DEFAULT nextval('public.llm_usage_logs_id_seq'::regclass);


--
-- Name: module_blueprints id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.module_blueprints ALTER COLUMN id SET DEFAULT nextval('public.module_blueprints_id_seq'::regclass);


--
-- Name: pl_prompt_variables id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_variables ALTER COLUMN id SET DEFAULT nextval('public.pl_prompt_variables_id_seq'::regclass);


--
-- Name: plagiarism_reports id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plagiarism_reports ALTER COLUMN id SET DEFAULT nextval('public.plagiarism_reports_id_seq'::regclass);


--
-- Name: project_user_assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_user_assignments ALTER COLUMN id SET DEFAULT nextval('public.project_user_assignments_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: prompt_fixings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_fixings ALTER COLUMN id SET DEFAULT nextval('public.prompt_fixings_id_seq'::regclass);


--
-- Name: prompt_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_versions ALTER COLUMN id SET DEFAULT nextval('public.prompt_versions_id_seq'::regclass);


--
-- Name: prompts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts ALTER COLUMN id SET DEFAULT nextval('public.prompts_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: style_documents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_documents ALTER COLUMN id SET DEFAULT nextval('public.style_documents_id_seq'::regclass);


--
-- Name: style_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_versions ALTER COLUMN id SET DEFAULT nextval('public.style_versions_id_seq'::regclass);


--
-- Name: styles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.styles ALTER COLUMN id SET DEFAULT nextval('public.styles_id_seq'::regclass);


--
-- Name: system_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs ALTER COLUMN id SET DEFAULT nextval('public.system_logs_id_seq'::regclass);


--
-- Name: user_prompt_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_prompt_history ALTER COLUMN id SET DEFAULT nextval('public.user_prompt_history_id_seq'::regclass);


--
-- Name: user_prompt_preferences id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_prompt_preferences ALTER COLUMN id SET DEFAULT nextval('public.user_prompt_preferences_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: workflow_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_events ALTER COLUMN id SET DEFAULT nextval('public.workflow_events_id_seq'::regclass);


--
-- Name: ab_test_runs ab_test_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ab_test_runs
    ADD CONSTRAINT ab_test_runs_pkey PRIMARY KEY (id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: block_comments block_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_comments
    ADD CONSTRAINT block_comments_pkey PRIMARY KEY (id);


--
-- Name: block_versions block_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_versions
    ADD CONSTRAINT block_versions_pkey PRIMARY KEY (id);


--
-- Name: blocks blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_pkey PRIMARY KEY (id);


--
-- Name: blueprint_versions blueprint_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprint_versions
    ADD CONSTRAINT blueprint_versions_pkey PRIMARY KEY (id);


--
-- Name: cdd_versions cdd_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdd_versions
    ADD CONSTRAINT cdd_versions_pkey PRIMARY KEY (id);


--
-- Name: central_repositories central_repositories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.central_repositories
    ADD CONSTRAINT central_repositories_pkey PRIMARY KEY (id);


--
-- Name: cluster_prompts cluster_prompts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_prompts
    ADD CONSTRAINT cluster_prompts_pkey PRIMARY KEY (id);


--
-- Name: clusters clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clusters
    ADD CONSTRAINT clusters_pkey PRIMARY KEY (id);


--
-- Name: course_design_documents course_design_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.course_design_documents
    ADD CONSTRAINT course_design_documents_pkey PRIMARY KEY (id);


--
-- Name: course_user_assignments course_user_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.course_user_assignments
    ADD CONSTRAINT course_user_assignments_pkey PRIMARY KEY (id);


--
-- Name: courses courses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_pkey PRIMARY KEY (id);


--
-- Name: document_chunks document_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: feedback_signals feedback_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feedback_signals
    ADD CONSTRAINT feedback_signals_pkey PRIMARY KEY (id);


--
-- Name: generation_jobs generation_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generation_jobs
    ADD CONSTRAINT generation_jobs_pkey PRIMARY KEY (id);


--
-- Name: generations generations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generations
    ADD CONSTRAINT generations_pkey PRIMARY KEY (id);


--
-- Name: job_metrics job_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_metrics
    ADD CONSTRAINT job_metrics_pkey PRIMARY KEY (id);


--
-- Name: llm_usage_logs llm_usage_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_usage_logs
    ADD CONSTRAINT llm_usage_logs_pkey PRIMARY KEY (id);


--
-- Name: module_blueprints module_blueprints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.module_blueprints
    ADD CONSTRAINT module_blueprints_pkey PRIMARY KEY (id);


--
-- Name: pl_attachments pl_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_attachments
    ADD CONSTRAINT pl_attachments_pkey PRIMARY KEY (id);


--
-- Name: pl_audit_events pl_audit_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_audit_events
    ADD CONSTRAINT pl_audit_events_pkey PRIMARY KEY (id);


--
-- Name: pl_prompt_requests pl_prompt_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_requests
    ADD CONSTRAINT pl_prompt_requests_pkey PRIMARY KEY (id);


--
-- Name: pl_prompt_tags pl_prompt_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_tags
    ADD CONSTRAINT pl_prompt_tags_pkey PRIMARY KEY (prompt_id, tag);


--
-- Name: pl_prompt_teams pl_prompt_teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_teams
    ADD CONSTRAINT pl_prompt_teams_pkey PRIMARY KEY (prompt_id, team_id);


--
-- Name: pl_prompt_variables pl_prompt_variables_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_variables
    ADD CONSTRAINT pl_prompt_variables_pkey PRIMARY KEY (id);


--
-- Name: pl_prompt_versions pl_prompt_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_versions
    ADD CONSTRAINT pl_prompt_versions_pkey PRIMARY KEY (id);


--
-- Name: pl_prompts pl_prompts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompts
    ADD CONSTRAINT pl_prompts_pkey PRIMARY KEY (id);


--
-- Name: pl_reviews pl_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_reviews
    ADD CONSTRAINT pl_reviews_pkey PRIMARY KEY (id);


--
-- Name: pl_teams pl_teams_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_teams
    ADD CONSTRAINT pl_teams_pkey PRIMARY KEY (id);


--
-- Name: plagiarism_reports plagiarism_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plagiarism_reports
    ADD CONSTRAINT plagiarism_reports_pkey PRIMARY KEY (id);


--
-- Name: plagiarism_reports plagiarism_reports_scan_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plagiarism_reports
    ADD CONSTRAINT plagiarism_reports_scan_id_key UNIQUE (scan_id);


--
-- Name: project_user_assignments project_user_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_user_assignments
    ADD CONSTRAINT project_user_assignments_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: prompt_fixings prompt_fixings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_fixings
    ADD CONSTRAINT prompt_fixings_pkey PRIMARY KEY (id);


--
-- Name: prompt_versions prompt_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_versions
    ADD CONSTRAINT prompt_versions_pkey PRIMARY KEY (id);


--
-- Name: prompts prompts_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts
    ADD CONSTRAINT prompts_name_key UNIQUE (name);


--
-- Name: prompts prompts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompts
    ADD CONSTRAINT prompts_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: style_documents style_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_documents
    ADD CONSTRAINT style_documents_pkey PRIMARY KEY (id);


--
-- Name: style_versions style_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_versions
    ADD CONSTRAINT style_versions_pkey PRIMARY KEY (id);


--
-- Name: styles styles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.styles
    ADD CONSTRAINT styles_pkey PRIMARY KEY (id);


--
-- Name: styles styles_style_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.styles
    ADD CONSTRAINT styles_style_id_key UNIQUE (style_id);


--
-- Name: system_logs system_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_logs
    ADD CONSTRAINT system_logs_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: pl_prompt_versions uq_pl_prompt_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_versions
    ADD CONSTRAINT uq_pl_prompt_version UNIQUE (prompt_id, version_number);


--
-- Name: pl_reviews uq_pl_review_prompt_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_reviews
    ADD CONSTRAINT uq_pl_review_prompt_user UNIQUE (prompt_id, username);


--
-- Name: pl_teams uq_pl_teams_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_teams
    ADD CONSTRAINT uq_pl_teams_name UNIQUE (name);


--
-- Name: user_prompt_history user_prompt_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_prompt_history
    ADD CONSTRAINT user_prompt_history_pkey PRIMARY KEY (id);


--
-- Name: user_prompt_preferences user_prompt_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_prompt_preferences
    ADD CONSTRAINT user_prompt_preferences_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: workflow_events workflow_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_events
    ADD CONSTRAINT workflow_events_pkey PRIMARY KEY (id);


--
-- Name: idx_audit_logs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_action ON public.audit_logs USING btree (action, created_at DESC);


--
-- Name: idx_audit_logs_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_entity ON public.audit_logs USING btree (entity_type, entity_id);


--
-- Name: idx_audit_logs_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_project ON public.audit_logs USING btree (project_id, created_at DESC);


--
-- Name: idx_audit_logs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_tenant_id ON public.audit_logs USING btree (tenant_id);


--
-- Name: idx_audit_logs_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_logs_user_created ON public.audit_logs USING btree (user_id, created_at DESC);


--
-- Name: idx_block_versions_block_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_block_versions_block_id ON public.block_versions USING btree (block_id, version_num DESC);


--
-- Name: idx_blocks_assigned_reviewer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blocks_assigned_reviewer ON public.blocks USING btree (assigned_reviewer);


--
-- Name: idx_blocks_generation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blocks_generation_id ON public.blocks USING btree (generation_id);


--
-- Name: idx_blocks_review_requested; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blocks_review_requested ON public.blocks USING btree (review_requested_at DESC);


--
-- Name: idx_blocks_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blocks_updated_at ON public.blocks USING btree (updated_at DESC);


--
-- Name: idx_blocks_workflow_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blocks_workflow_state ON public.blocks USING btree (workflow_state);


--
-- Name: idx_blueprint_project_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blueprint_project_course ON public.module_blueprints USING btree (project_id, course_id);


--
-- Name: idx_blueprint_versions_version_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_blueprint_versions_version_number ON public.blueprint_versions USING btree (blueprint_id, version_number);


--
-- Name: idx_cdd_project_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdd_project_course ON public.course_design_documents USING btree (project_id, course_id);


--
-- Name: idx_cdd_versions_version_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_cdd_versions_version_number ON public.cdd_versions USING btree (cdd_id, version_number);


--
-- Name: idx_central_repo_cluster; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_central_repo_cluster ON public.central_repositories USING btree (cluster_id);


--
-- Name: idx_central_repo_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_central_repo_project ON public.central_repositories USING btree (project_id);


--
-- Name: idx_central_repo_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_central_repo_status ON public.central_repositories USING btree (status, created_at DESC);


--
-- Name: idx_central_repo_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_central_repo_type ON public.central_repositories USING btree (item_type, status);


--
-- Name: idx_central_repos_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_central_repos_tenant_id ON public.central_repositories USING btree (tenant_id);


--
-- Name: idx_clusters_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clusters_project_id ON public.clusters USING btree (project_id, is_active);


--
-- Name: idx_course_user_assignments_user_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_course_user_assignments_user_course ON public.course_user_assignments USING btree (username, course_id);


--
-- Name: idx_courses_cluster_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_courses_cluster_id ON public.courses USING btree (cluster_id);


--
-- Name: idx_document_chunks_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_document_chunks_document_id ON public.document_chunks USING btree (document_id);


--
-- Name: idx_documents_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_status ON public.documents USING btree (status);


--
-- Name: idx_documents_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_tenant_id ON public.documents USING btree (tenant_id);


--
-- Name: idx_generation_jobs_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generation_jobs_created_by ON public.generation_jobs USING btree (created_by, created_at DESC);


--
-- Name: idx_generation_jobs_project_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generation_jobs_project_course ON public.generation_jobs USING btree (project_id, course_id);


--
-- Name: idx_generation_jobs_status_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generation_jobs_status_created ON public.generation_jobs USING btree (status, created_at DESC);


--
-- Name: idx_generation_jobs_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generation_jobs_tenant_id ON public.generation_jobs USING btree (tenant_id);


--
-- Name: idx_generations_blueprint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generations_blueprint_id ON public.generations USING btree (blueprint_id);


--
-- Name: idx_generations_cdd_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generations_cdd_id ON public.generations USING btree (cdd_id);


--
-- Name: idx_generations_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generations_created_at ON public.generations USING btree (created_at DESC);


--
-- Name: idx_generations_project_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_generations_project_course ON public.generations USING btree (project_id, course_id);


--
-- Name: idx_job_metrics_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_metrics_job_id ON public.job_metrics USING btree (job_id);


--
-- Name: idx_llm_usage_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_entity ON public.llm_usage_logs USING btree (entity_type, entity_id);


--
-- Name: idx_llm_usage_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_model ON public.llm_usage_logs USING btree (model_name, created_at DESC);


--
-- Name: idx_llm_usage_project_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_project_created ON public.llm_usage_logs USING btree (project_id, created_at DESC);


--
-- Name: idx_llm_usage_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_status ON public.llm_usage_logs USING btree (status, created_at DESC);


--
-- Name: idx_llm_usage_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_tenant_id ON public.llm_usage_logs USING btree (tenant_id);


--
-- Name: idx_llm_usage_user_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_llm_usage_user_created ON public.llm_usage_logs USING btree (user_id, created_at DESC);


--
-- Name: idx_project_user_assignments_user_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_project_user_assignments_user_project ON public.project_user_assignments USING btree (username, project_id);


--
-- Name: idx_projects_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_tenant_id ON public.projects USING btree (tenant_id);


--
-- Name: idx_prompts_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_prompts_tenant_id ON public.prompts USING btree (tenant_id);


--
-- Name: idx_style_versions_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_style_versions_active ON public.style_versions USING btree (style_id, is_active);


--
-- Name: idx_style_versions_style_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_style_versions_style_id ON public.style_versions USING btree (style_id, version_number DESC);


--
-- Name: idx_styles_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_styles_tenant_id ON public.styles USING btree (tenant_id);


--
-- Name: idx_uph_component_course; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uph_component_course ON public.user_prompt_history USING btree (component, course_id);


--
-- Name: idx_uph_component_project; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uph_component_project ON public.user_prompt_history USING btree (component, project_id);


--
-- Name: idx_uph_name_component; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_uph_name_component ON public.user_prompt_history USING btree (name, component);


--
-- Name: idx_users_tenant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_tenant_id ON public.users USING btree (tenant_id);


--
-- Name: ix_audit_logs_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_action ON public.audit_logs USING btree (action);


--
-- Name: ix_audit_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_created_at ON public.audit_logs USING btree (created_at);


--
-- Name: ix_audit_logs_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_entity_type ON public.audit_logs USING btree (entity_type);


--
-- Name: ix_audit_logs_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_project_id ON public.audit_logs USING btree (project_id);


--
-- Name: ix_audit_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_logs_user_id ON public.audit_logs USING btree (user_id);


--
-- Name: ix_cluster_prompts_cluster_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cluster_prompts_cluster_id ON public.cluster_prompts USING btree (cluster_id);


--
-- Name: ix_llm_usage_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_logs_created_at ON public.llm_usage_logs USING btree (created_at);


--
-- Name: ix_llm_usage_logs_model_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_logs_model_name ON public.llm_usage_logs USING btree (model_name);


--
-- Name: ix_llm_usage_logs_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_logs_project_id ON public.llm_usage_logs USING btree (project_id);


--
-- Name: ix_llm_usage_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_llm_usage_logs_user_id ON public.llm_usage_logs USING btree (user_id);


--
-- Name: ix_pl_attachments_prompt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_attachments_prompt_id ON public.pl_attachments USING btree (prompt_id);


--
-- Name: ix_pl_audit_events_actor_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_audit_events_actor_username ON public.pl_audit_events USING btree (actor_username);


--
-- Name: ix_pl_audit_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_audit_events_created_at ON public.pl_audit_events USING btree (created_at);


--
-- Name: ix_pl_audit_events_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_audit_events_entity_id ON public.pl_audit_events USING btree (entity_id);


--
-- Name: ix_pl_audit_events_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_audit_events_entity_type ON public.pl_audit_events USING btree (entity_type);


--
-- Name: ix_pl_audit_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_audit_events_event_type ON public.pl_audit_events USING btree (event_type);


--
-- Name: ix_pl_prompt_requests_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompt_requests_status ON public.pl_prompt_requests USING btree (status);


--
-- Name: ix_pl_prompt_variables_prompt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompt_variables_prompt_id ON public.pl_prompt_variables USING btree (prompt_id);


--
-- Name: ix_pl_prompt_versions_prompt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompt_versions_prompt_id ON public.pl_prompt_versions USING btree (prompt_id);


--
-- Name: ix_pl_prompts_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_category ON public.pl_prompts USING btree (category);


--
-- Name: ix_pl_prompts_deleted_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_deleted_at ON public.pl_prompts USING btree (deleted_at);


--
-- Name: ix_pl_prompts_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_parent_id ON public.pl_prompts USING btree (parent_id);


--
-- Name: ix_pl_prompts_team_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_team_id ON public.pl_prompts USING btree (team_id);


--
-- Name: ix_pl_prompts_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_title ON public.pl_prompts USING btree (title);


--
-- Name: ix_pl_prompts_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_updated_at ON public.pl_prompts USING btree (updated_at);


--
-- Name: ix_pl_prompts_visibility; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_prompts_visibility ON public.pl_prompts USING btree (visibility);


--
-- Name: ix_pl_reviews_prompt_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pl_reviews_prompt_id ON public.pl_reviews USING btree (prompt_id);


--
-- Name: ix_plagiarism_reports_block_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plagiarism_reports_block_id ON public.plagiarism_reports USING btree (block_id);


--
-- Name: ix_plagiarism_reports_course_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plagiarism_reports_course_id ON public.plagiarism_reports USING btree (course_id);


--
-- Name: ix_plagiarism_reports_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plagiarism_reports_project_id ON public.plagiarism_reports USING btree (project_id);


--
-- Name: ix_plagiarism_reports_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_plagiarism_reports_status ON public.plagiarism_reports USING btree (status);


--
-- Name: ix_prompt_fixings_cluster_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_prompt_fixings_cluster_id ON public.prompt_fixings USING btree (cluster_id);


--
-- Name: ix_prompt_fixings_component; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_prompt_fixings_component ON public.prompt_fixings USING btree (component);


--
-- Name: ix_prompt_fixings_course_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_prompt_fixings_course_id ON public.prompt_fixings USING btree (course_id);


--
-- Name: ix_prompt_fixings_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_prompt_fixings_project_id ON public.prompt_fixings USING btree (project_id);


--
-- Name: ix_tenants_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_tenants_slug ON public.tenants USING btree (slug);


--
-- Name: ix_user_prompt_preferences_course_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_prompt_preferences_course_id ON public.user_prompt_preferences USING btree (course_id);


--
-- Name: ix_user_prompt_preferences_user_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_prompt_preferences_user_name ON public.user_prompt_preferences USING btree (user_name);


--
-- Name: block_comments block_comments_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_comments
    ADD CONSTRAINT block_comments_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: block_versions block_versions_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.block_versions
    ADD CONSTRAINT block_versions_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: blocks blocks_generation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blocks
    ADD CONSTRAINT blocks_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES public.generations(id);


--
-- Name: blueprint_versions blueprint_versions_blueprint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprint_versions
    ADD CONSTRAINT blueprint_versions_blueprint_id_fkey FOREIGN KEY (blueprint_id) REFERENCES public.module_blueprints(id);


--
-- Name: cdd_versions cdd_versions_cdd_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cdd_versions
    ADD CONSTRAINT cdd_versions_cdd_id_fkey FOREIGN KEY (cdd_id) REFERENCES public.course_design_documents(id);


--
-- Name: central_repositories central_repositories_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.central_repositories
    ADD CONSTRAINT central_repositories_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.clusters(id);


--
-- Name: central_repositories central_repositories_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.central_repositories
    ADD CONSTRAINT central_repositories_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(id);


--
-- Name: central_repositories central_repositories_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.central_repositories
    ADD CONSTRAINT central_repositories_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: cluster_prompts cluster_prompts_cluster_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cluster_prompts
    ADD CONSTRAINT cluster_prompts_cluster_id_fkey FOREIGN KEY (cluster_id) REFERENCES public.clusters(id) ON DELETE CASCADE;


--
-- Name: clusters clusters_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clusters
    ADD CONSTRAINT clusters_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: course_user_assignments course_user_assignments_course_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.course_user_assignments
    ADD CONSTRAINT course_user_assignments_course_id_fkey FOREIGN KEY (course_id) REFERENCES public.courses(id);


--
-- Name: courses courses_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.courses
    ADD CONSTRAINT courses_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: document_chunks document_chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document_chunks
    ADD CONSTRAINT document_chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: feedback_signals feedback_signals_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feedback_signals
    ADD CONSTRAINT feedback_signals_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: feedback_signals feedback_signals_generation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.feedback_signals
    ADD CONSTRAINT feedback_signals_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES public.generations(id);


--
-- Name: generations generations_blueprint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generations
    ADD CONSTRAINT generations_blueprint_id_fkey FOREIGN KEY (blueprint_id) REFERENCES public.module_blueprints(id);


--
-- Name: generations generations_cdd_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.generations
    ADD CONSTRAINT generations_cdd_id_fkey FOREIGN KEY (cdd_id) REFERENCES public.course_design_documents(id);


--
-- Name: module_blueprints module_blueprints_cdd_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.module_blueprints
    ADD CONSTRAINT module_blueprints_cdd_id_fkey FOREIGN KEY (cdd_id) REFERENCES public.course_design_documents(id);


--
-- Name: pl_attachments pl_attachments_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_attachments
    ADD CONSTRAINT pl_attachments_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompt_requests pl_prompt_requests_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_requests
    ADD CONSTRAINT pl_prompt_requests_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE SET NULL;


--
-- Name: pl_prompt_tags pl_prompt_tags_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_tags
    ADD CONSTRAINT pl_prompt_tags_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompt_teams pl_prompt_teams_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_teams
    ADD CONSTRAINT pl_prompt_teams_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompt_teams pl_prompt_teams_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_teams
    ADD CONSTRAINT pl_prompt_teams_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.pl_teams(id) ON DELETE CASCADE;


--
-- Name: pl_prompt_variables pl_prompt_variables_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_variables
    ADD CONSTRAINT pl_prompt_variables_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompt_versions pl_prompt_versions_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompt_versions
    ADD CONSTRAINT pl_prompt_versions_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompts pl_prompts_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompts
    ADD CONSTRAINT pl_prompts_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: pl_prompts pl_prompts_team_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_prompts
    ADD CONSTRAINT pl_prompts_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.pl_teams(id) ON DELETE SET NULL;


--
-- Name: pl_reviews pl_reviews_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pl_reviews
    ADD CONSTRAINT pl_reviews_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.pl_prompts(id) ON DELETE CASCADE;


--
-- Name: plagiarism_reports plagiarism_reports_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plagiarism_reports
    ADD CONSTRAINT plagiarism_reports_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id) ON DELETE CASCADE;


--
-- Name: project_user_assignments project_user_assignments_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.project_user_assignments
    ADD CONSTRAINT project_user_assignments_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: prompt_fixings prompt_fixings_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_fixings
    ADD CONSTRAINT prompt_fixings_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.prompts(id) ON DELETE SET NULL;


--
-- Name: prompt_versions prompt_versions_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.prompt_versions
    ADD CONSTRAINT prompt_versions_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.prompts(id);


--
-- Name: reviews reviews_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- Name: reviews reviews_generation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_generation_id_fkey FOREIGN KEY (generation_id) REFERENCES public.generations(id);


--
-- Name: style_documents style_documents_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_documents
    ADD CONSTRAINT style_documents_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id);


--
-- Name: style_documents style_documents_style_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_documents
    ADD CONSTRAINT style_documents_style_id_fkey FOREIGN KEY (style_id) REFERENCES public.styles(id);


--
-- Name: style_versions style_versions_style_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.style_versions
    ADD CONSTRAINT style_versions_style_id_fkey FOREIGN KEY (style_id) REFERENCES public.styles(id);


--
-- Name: user_prompt_preferences user_prompt_preferences_prompt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_prompt_preferences
    ADD CONSTRAINT user_prompt_preferences_prompt_id_fkey FOREIGN KEY (prompt_id) REFERENCES public.prompts(id) ON DELETE SET NULL;


--
-- Name: workflow_events workflow_events_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workflow_events
    ADD CONSTRAINT workflow_events_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.blocks(id);


--
-- PostgreSQL database dump complete
--


