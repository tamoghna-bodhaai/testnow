"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { FigureEditor } from "./figure-editor";
import { LatestRequest, bankUrl, readBankLocation } from "./bank-navigation";
import { reviewUploads, UploadConfig } from "./upload-preflight";
import { AdminQuestionEditor } from "./question-editor";
import { QuestionLibrary } from "./question-library";
import { TeacherSupport } from "./teacher-support";
import { useDialogFocus } from "./dialog-focus";
import { AppConfirmHost, confirmApp } from "./app-confirm";

type User = {
  id: string;
  name: string;
  email: string;
  role: "teacher" | "student" | "admin";
  status?: "active" | "suspended" | "pending_code";
  last_login_at?: string;
};
type WorkspaceView =
  | "home"
  | "teacher-detail"
  | "import"
  | "reviews"
  | "builder"
  | "classes"
  | "bank-import"
  | "test-detail"
  | "exam"
  | "result";
type AdminDashboard = {
  summary: {
    teachers: number;
    active_teachers: number;
    suspended_teachers: number;
    students: number;
  };
  teachers: (User & {
    batch_count: number;
    student_count: number;
    test_count: number;
    attempt_count: number;
    submitted_count: number;
  })[];
};
type ReadinessReason = { code: string; field: string; message: string };
type Readiness = { validator_version: string; content_valid: boolean; reviewed: boolean; publishable: boolean; blocking: ReadinessReason[]; advisory: ReadinessReason[] };
type ReadinessCounts = { total: number; content_valid: number; publishable: number; blocked: number; needs_review: number; needs_attention: number };
export type Question = {
  revision?: string;
  id: string;
  import_job_id?: string;
  source_number?: string;
  readiness?: Readiness;
  kind: string;
  stem_markdown: string;
  options: any[];
  answer?: any;
  solution_markdown?: string;
  confidence?: number;
  approved?: boolean;
  scoring?: any;
  diagrams?: { alt?: string; role?: string; option_index?: number | null; object_key?: string; page?: number; bbox?: number[] }[];
  interaction?: any;
  scope?: "private" | "global";
  taxonomy?: { id: string; parent_id?: string; kind: string; name: string }[];
  generation_eligible?: boolean;
  generation_missing_fields?: ("answer" | "solution")[];
};
type QuestionPage = { items: Question[]; page: number; page_size: number; total: number; approved_total?: number; has_more: boolean };
type ImportJob = {
  id: string;
  status: "uploaded" | "processing" | "review" | "failed" | "approved";
  filename?: string;
  bank_id?: string;
  lifecycle?: { state: string; heartbeat_at?: string; last_activity_at?: string; processed?: number; total?: number; unit?: string };
  errors_total?: number;
  source_id?: string;
  error?: string;
  errors?: { page?: number; end_page?: number; message: string }[];
  question_count?: number;
  approved_count?: number;
  readiness?: ReadinessCounts;
  generation_eligible_count?: number;
  published?: boolean;
  published_at?: string;
  meta?: {
    phase?: string;
    question_count?: number;
    paper_title?: string;
    flagged_count?: number;
    skipped_count?: number;
    failed_pages?: { page: number; end_page?: number; error: string }[];
    subject?: string;
    processed_pages?: number;
    total_pages?: number;
    chunks_complete?: number;
    chunks_total?: number;
    pause_requested?: boolean;
    cancel_requested?: boolean;
    delete_requested?: boolean;
    error_log?: { page?: number; message: string }[];
    solution_generation?: {
      run_id: string;
      mode: "document" | "question";
      question_id?: string | null;
      status: "queued" | "running" | "completed" | "completed_with_errors" | "failed";
      phase?: string;
      total: number;
      processed: number;
      succeeded: number;
      failed: number;
      skipped?: number;
      error?: string;
      approved?: number;
      approved_at?: string;
      errors?: { question_id?: string; source_number?: string; message: string }[];
    };
  };
  questions?: Question[];
};
type Classroom = {
  id: string;
  name: string;
  description?: string;
  student_count: number;
  students?: User[];
};
type StudentClass = { id: string; name: string; description?: string };
type IntegrityEventType = "tab_hidden" | "window_blur" | "fullscreen_exit" | "page_hidden";
type Test = {
  id: string;
  title: string;
  subject: string;
  status: "draft" | "published" | "archived";
  duration_seconds: number;
  question_count: number;
  opens_at?: string;
  closes_at?: string;
  max_attempts: number;
  class_count: number;
  assigned_student_count: number;
  assigned_class_names?: string[];
  classes?: Classroom[];
  questions?: Question[];
  delivery_blueprint?: { sections: { id: string; title: string; question_ids: string[] }[] };
  availability?: "available" | "upcoming" | "closed";
  attempts_remaining?: number;
  latest_attempt?: {
    id: string;
    status: "submitted" | "in_progress" | "expired" | "cancelled";
    started_at?: string;
    submitted_at?: string;
    duration_seconds?: number;
    score?: number;
    integrity_flagged?: boolean;
    cancellation_reason?: string;
  } | null;
};

function LandingPage({ onSignIn }: { onSignIn: () => void }) {
  return (
    <main className="landing-page">
      <header className="landing-nav">
        <button className="landing-brand" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} aria-label="TestNow home">
          Test<span>Now</span>
        </button>
        <button className="landing-signin" onClick={onSignIn}>Sign in</button>
      </header>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-copy">
          <p className="landing-eyebrow">ASSESSMENTS, MADE CLEAR</p>
          <h1 id="landing-title">Turn question banks into <em>better learning.</em></h1>
          <p className="landing-intro">Create and organise question banks, build assessments in minutes, and understand learner performance—so you can teach with confidence.</p>
          <button className="landing-primary" onClick={onSignIn}>Sign in to continue <span aria-hidden="true">→</span></button>
        </div>

        <section className="product-preview" aria-label="TestNow platform preview">
          <div className="preview-topbar">
            <b>TestNow</b>
            <div><span>Class 10A</span><span className="preview-avatar">JS</span></div>
          </div>
          <div className="preview-content">
            <aside className="preview-sidebar" aria-label="Platform sections">
              <b className="preview-active">Overview</b><b>Question bank</b><b>Assessments</b><b>Results</b><b>Students</b>
            </aside>
            <div className="preview-main">
              <div className="preview-stats">
                <PreviewStat label="Questions" value="1,248" accent="gold" />
                <PreviewStat label="Assessments" value="36" accent="indigo" />
                <PreviewStat label="Students" value="180" accent="teal" />
              </div>
              <div className="preview-panels">
                <section className="preview-panel">
                  <div className="preview-heading"><b>Question bank coverage</b><span>View all →</span></div>
                  <ProgressRow label="Algebra" value="92%" progress="92" tone="teal" />
                  <ProgressRow label="Geometry" value="78%" progress="78" tone="indigo" />
                  <ProgressRow label="Number systems" value="65%" progress="65" tone="sky" />
                  <ProgressRow label="Data & probability" value="54%" progress="54" tone="sky" />
                </section>
                <section className="preview-panel preview-chart">
                  <div className="preview-heading"><b>Class proficiency</b><span>Last 4 weeks</span></div>
                  <div className="bars" aria-label="Class proficiency chart"><i style={{height:"46%"}}/><i style={{height:"67%"}}/><i style={{height:"82%"}}/><i style={{height:"56%"}}/></div>
                  <div className="bar-labels"><span>Algebra</span><span>Geometry</span><span>Number</span><span>Data</span></div>
                </section>
              </div>
            </div>
          </div>
        </section>
      </section>

      <section className="landing-proof" aria-label="TestNow capabilities">
        <div><b>Question banks</b><span>Organise and reuse high-quality questions by topic, standard, or skill.</span></div>
        <div><b>Build assessments</b><span>Create quizzes, tests, and practice sets in minutes.</span></div>
        <div><b>Understand performance</b><span>See class and individual insights to inform your next steps.</span></div>
      </section>

      <section className="landing-features" aria-labelledby="features-title">
        <div className="feature-intro">
          <p className="landing-eyebrow">ONE WORKSPACE, END TO END</p>
          <h2 id="features-title">From paper to practical insight.</h2>
          <p>TestNow brings the work before, during, and after an assessment into one clear workflow for teachers and students.</p>
        </div>

        <article className="feature-row upload-feature">
          <div className="feature-copy">
            <p className="feature-number">01 / QUESTION BANKS</p>
            <h3>Upload a paper. Start with what you already have.</h3>
            <p>Bring in existing question papers and turn them into structured, reusable questions. Review the content, organise it by subject and topic, then keep building your bank over time.</p>
            <ul><li>Direct paper and document upload</li><li>Review questions before publishing</li><li>Organise by subject, chapter, and topic</li></ul>
          </div>
          <div className="feature-preview upload-preview" aria-label="Question paper upload preview">
            <div className="upload-file"><span>Physics_unit-test.pdf</span><b>Ready to review</b></div>
            <div className="upload-progress"><i /><span>Reading questions and options</span><b>84%</b></div>
            <div className="upload-questions"><b>Q14</b><p>A body is projected at 30° with a speed of 20 m s⁻¹. Its maximum height is</p><span>Physics · Motion in a plane</span></div>
            <div className="upload-tags"><span>MCQ</span><span>Grade 11</span><span>Needs review</span></div>
          </div>
        </article>

        <article className="feature-row builder-feature">
          <div className="feature-preview blueprint-preview" aria-label="Assessment creation preview">
            <div className="blueprint-top"><b>New assessment</b><span>Save draft</span></div>
            <div className="blueprint-fields"><div><small>Title</small><b>Algebra unit test</b></div><div><small>Class</small><b>Class 10A</b></div></div>
            <div className="blueprint-summary"><span>12 questions</span><span>60 marks</span><span>45 minutes</span></div>
            <ol><li><b>01</b><span>Linear equations</span><em>5 marks</em></li><li><b>02</b><span>Quadratic graphs</span><em>5 marks</em></li><li><b>03</b><span>Data interpretation</span><em>5 marks</em></li></ol>
          </div>
          <div className="feature-copy">
            <p className="feature-number">02 / TEST CREATION</p>
            <h3>Build the right test without rebuilding the work.</h3>
            <p>Select questions from your bank, set the marks and duration, and assign the assessment to the right class. Drafts stay editable until you are ready to publish.</p>
            <ul><li>Mix questions across your own bank</li><li>Set timing, marks, and availability</li><li>Assign to one class or many</li></ul>
          </div>
        </article>

        <article className="feature-row cbt-feature">
          <div className="feature-copy">
            <p className="feature-number">03 / SECURE CBT DELIVERY</p>
            <h3>Give students a focused test-day experience.</h3>
            <p>Standard CBT controls make the assessment familiar and clear: an always-visible timer, question palette, saved answers, review flags, and a deliberate submit flow.</p>
            <ul><li>Timer and question navigation</li><li>Answered, flagged, and unanswered states</li><li>Clear save and submit controls</li></ul>
          </div>
          <div className="feature-preview cbt-preview" aria-label="Computer-based test preview">
            <div className="cbt-top"><b>Algebra unit test</b><span>00:28:17</span><button type="button">Submit test</button></div>
            <div className="cbt-body"><aside><b>Question 14</b><span>Answered 10</span><span>Not answered 2</span><div>{Array.from({length: 10}, (_, index) => <i className={index === 3 ? "active" : index < 7 ? "answered" : ""} key={index}>{index + 11}</i>)}</div></aside><section><small>MATHEMATICS · ALGEBRA</small><h4>Which equation has exactly one solution for x?</h4><button type="button" className="chosen">A&nbsp;&nbsp; 2(x − 3) = 2x − 6</button><button type="button">B&nbsp;&nbsp; 4x + 5 = 4x + 1</button><button type="button">C&nbsp;&nbsp; 3(x + 2) = 3x + 6</button></section></div>
          </div>
        </article>

        <section className="results-callout">
          <div><p className="landing-eyebrow">AFTER THE TEST</p><h2>Know what to teach next.</h2><p>Results become a clear picture of class progress: what learners understood, where they struggled, and which topics deserve attention before the next assessment.</p></div>
          <div className="result-metrics"><span><b>78%</b>average score</span><span><b>12</b>questions to revisit</span><span><b>4</b>topics needing support</span></div>
        </section>
      </section>
    </main>
  );
}

function PreviewStat({ label, value, accent }: { label: string; value: string; accent: string }) {
  return <div className={`preview-stat ${accent}`}><span>{label}</span><b>{value}</b><small>↑ this term</small></div>;
}

function ProgressRow({ label, value, progress, tone }: { label: string; value: string; progress: string; tone: string }) {
  return <div className="progress-row"><span>{label}</span><div><i className={tone} style={{ width: `${progress}%` }} /></div><b>{value}</b></div>;
}
type Analytics = {
  summary: {
    assigned: number;
    currently_assigned: number;
    started: number;
    submitted: number;
    pending: number;
    cancelled: number;
    flagged: number;
    average_score?: number;
    average_percentage?: number;
    highest_score?: number;
    completion_rate: number;
    flag_rate: number;
  };
  batches: {
    classroom: { id: string; name: string };
    assigned: number;
    started: number;
    submitted: number;
    pending: number;
    cancelled: number;
    flagged: number;
    average_score?: number;
    average_percentage?: number;
    completion_rate: number;
    flag_rate: number;
  }[];
  students: {
    student: User;
    classes: { id: string; name: string }[];
    status: string;
    score?: number;
    percentage?: number;
    started_at?: string;
    submitted_at?: string;
    duration_seconds?: number;
    attempt_count: number;
    integrity_flagged: boolean;
    best_score?: number;
    average_score?: number;
    attempts: {
      id: string;
      status: string;
      score?: number;
      max_score: number;
      percentage?: number;
      correct: number;
      incorrect: number;
      unanswered: number;
      started_at: string;
      submitted_at?: string;
      duration_seconds?: number;
      integrity_flagged: boolean;
      cancellation_reason?: string;
      incidents: { event_type: string; detected_at: string }[];
    }[];
  }[];
};
type Bank = {
  id: string;
  slug: string;
  name: string;
  exam_code: string;
  status: string;
  revision: number;
  is_global: boolean;
  question_count: number;
  published_count: number;
  import_count: number;
  flagged_count: number;
  readiness?: ReadinessCounts;
  imports?: ImportJob[];
  questions?: Question[];
};

const errorMessage = (detail: unknown) =>
  Array.isArray(detail)
    ? detail.map((item: any) => item.msg || "Invalid request").join(" ")
    : typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && "message" in detail
        ? String(detail.message)
        : "Request failed";
const api = async (path: string, options: RequestInit = {}) => {
  if ((!options.method || options.method === "GET") && /^\/admin\/question-banks\/[^/]+\/imports\/[^/?]+$/.test(path)) path += "?include_questions=false";
  const r = await fetch(`/api${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({ detail: "Request failed" }));
    throw Object.assign(new Error(errorMessage(body.detail)), { detail: body.detail, status: r.status });
  }
  return r.status === 204 ? null : r.json();
};
const bankImportForm = (form: HTMLFormElement) => {
  const data = new FormData();
  data.set("subject", String(new FormData(form).get("subject") || "").trim());
  for (const name of ["question_files", "answer_files"]) {
    for (const input of Array.from(
      form.querySelectorAll<HTMLInputElement>(
        `input[name="${name}"][type="file"]`,
      ),
    )) {
      for (const file of Array.from(input.files || [])) {
        if (!file.size || !file.name) continue;
        if (!/\.(pdf|docx)$/i.test(file.name))
          throw new Error(`${file.name} is not a PDF or DOCX file`);
        data.append(name, file, file.name);
      }
    }
  }
  if (!data.getAll("question_files").length)
    throw new Error("Choose at least one PDF or DOCX question document");
  return data;
};
const uploadBankImport = (
  bankId: string,
  data: FormData,
  onProgress: (loaded: number, total: number) => void,
) =>
  new Promise<any>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/bank-import/${bankId}`);
    request.withCredentials = true;
    request.upload.onprogress = (event) =>
      onProgress(event.loaded, event.total || 0);
    request.onerror = () => reject(new Error("Upload connection failed"));
    request.onload = () => {
      let body: any = {};
      try {
        body = JSON.parse(request.responseText);
      } catch {}
      if (request.status >= 200 && request.status < 300) resolve(body);
      else reject(new Error(errorMessage(body.detail || "Upload failed")));
    };
    request.send(data);
  });
const fmtDate = (value?: string) =>
  value
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
const fmtDuration = (seconds?: number | null) =>
  seconds === undefined || seconds === null
    ? "—"
    : `${Math.floor(seconds / 3600) ? `${Math.floor(seconds / 3600)}h ` : ""}${Math.floor((seconds % 3600) / 60)}m ${seconds % 60}s`;
const questionNumber = (value?: string) =>
  value ? `Q${String(value).trim().replace(/^q\s*/i, "")}` : "Question";
const JEE_QUESTION_TYPES = [
  ["single_choice", "MCQ"],
  ["multiple_choice", "Multi correct"],
  ["numerical", "Numerical"],
  ["integer", "Integer"],
  ["matrix_match", "Matrix match"],
  ["assertion_reason", "Assertion reason"],
  ["subjective", "Subjective"],
] as const;
const locationParam = (name: string, fallback = "") => typeof window === "undefined" ? fallback : new URLSearchParams(window.location.search).get(name) || fallback;
const replaceLocationParams = (values: Record<string,string>) => { const url=new URL(window.location.href);for(const [key,value] of Object.entries(values)){if(value)url.searchParams.set(key,value);else url.searchParams.delete(key);}window.history.replaceState({},"",url.pathname+url.search+url.hash); };
const questionPublishable = (question: Question) => question.readiness?.publishable ?? (Boolean(question.approved) && !(question.interaction?.ingestion_issues || []).length);
const questionNeedsAttention = (question: Question) => !questionPublishable(question);
const questionReadinessLabel = (question: Question) => question.readiness?.content_valid === false ? "Blocked: needs repair" : questionPublishable(question) ? "Ready to publish" : "Awaiting review";
const readyQuestionCount = (job: ImportJob) => job.readiness?.publishable ?? job.approved_count ?? 0;
async function publishWithPreview(path: string, scope: string) {
  const preview = await api(`${path}/publication-preview`);
  const reasons = Object.entries(preview.blocking_reasons as Record<string, number>).map(([code, count]) => `${code.replaceAll("_", " ")}: ${count}`).join("\n");
  if (!preview.eligible && !preview.already_published) throw new Error(`No eligible questions in ${scope}. ${preview.blocked} blocked. ${reasons}`);
  if (!await confirmApp(`Publish eligible questions in ${scope}?\n\n${preview.eligible} eligible\n${preview.blocked} blocked\n${preview.already_published} already published${reasons ? `\n\nBlocking reasons (a question may have several):\n${reasons}` : ""}\n\nThe server will revalidate before publishing. Blocked questions remain excluded.`)) return null;
  return api(`${path}/publish`, { method: "POST" });
}
function publicationMessage(result: any) {
  const publication = result.publication;
  return publication ? `${publication.published_count} questions published; ${publication.blocked} blocked; ${publication.already_published} already published.${publication.partial ? " Review the blocked questions before releasing them." : ""}` : "Publication completed.";
}

function Card({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <section className={`card ${className}`}>{children}</section>;
}
function Markdown({
  value,
  className = "",
}: {
  value: string;
  className?: string;
}) {
  const normalized = value
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `\n$$\n${math.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math.trim()}$`);
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
function Notice({
  children,
  kind = "info",
}: {
  children: React.ReactNode;
  kind?: "info" | "error" | "success";
}) {
  return <p className={`notice ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</p>;
}

export function Portal() {
  const [user, setUser] = useState<User | null>(null),
    [showLanding, setShowLanding] = useState(true),
    [error, setError] = useState(""),
    [loginError, setLoginError] = useState(""),
    [view, setView] = useState<WorkspaceView>("home"),
    [supportTeacher,setSupportTeacher]=useState<string|null>(null),
    [tests, setTests] = useState<Test[]>([]),
    [questions, setQuestions] = useState<Question[]>([]),
    [questionCount, setQuestionCount] = useState(0),
    [classes, setClasses] = useState<Classroom[]>([]),
    [studentClasses, setStudentClasses] = useState<StudentClass[]>([]),
    [jobs, setJobs] = useState<ImportJob[]>([]),
    [admin, setAdmin] = useState<AdminDashboard | null>(null),
    [selectedJobId, setSelectedJobId] = useState<string | null>(null),
    [selectedTestId, setSelectedTestId] = useState<string | null>(null),
    [builderImportId, setBuilderImportId] = useState<string | null>(null),
    [openAssignmentAfterCreate, setOpenAssignmentAfterCreate] = useState(false),
    [attempt, setAttempt] = useState<any>(null),
    [pendingTest, setPendingTest] = useState<Test | null>(null),
    [index, setIndex] = useState(0),
    [message, setMessage] = useState("");
  const previousJobs = useRef<Map<string, string>>(new Map()),
    sessionVersion = useRef(0),
    finishingAttempt = useRef<string | null>(null),
    attemptRef = useRef<any>(null),
    responseSaveQueue = useRef<Promise<boolean>>(Promise.resolve(true)),
    failedResponseSaves = useRef<Set<string>>(new Set()),
    activeAttemptId = useRef<string | null>(null);
  useEffect(() => {
    attemptRef.current = attempt;
    if ((attempt?.id || null) !== activeAttemptId.current) {
      activeAttemptId.current = attempt?.id || null;
      responseSaveQueue.current = Promise.resolve(true);
      failedResponseSaves.current.clear();
      finishingAttempt.current = null;
    }
  }, [attempt]);
  const loadImports = async (version = sessionVersion.current) => {
    if (version !== sessionVersion.current) return [];
    const next: ImportJob[] = await api("/teacher/imports");
    if (version !== sessionVersion.current) return [];
    const done = next.find(
      (job) =>
        previousJobs.current.get(job.id) === "processing" &&
        job.status === "review",
    );
    if (done) setMessage("Import complete — questions are ready for review.");
    previousJobs.current = new Map(next.map((job) => [job.id, job.status]));
    setJobs(next);
    return next;
  };
  const load = async (u?: User) => {
    const version = sessionVersion.current,
      me = u || (await api("/auth/me"));
    if (version !== sessionVersion.current) return;
    setUser(me);
    if (me.role === "admin") {
      const teacherPath=window.location.pathname.match(/^\/admin\/teachers\/([^/]+)$/);
      if(teacherPath){setSupportTeacher(decodeURIComponent(teacherPath[1]));setView("teacher-detail");}
      else if (window.location.pathname.startsWith("/admin/banks") || readBankLocation(window.location.pathname, window.location.hash)) setView("bank-import");
      setAdmin(await api("/admin/dashboard"));
    } else if (me.role === "teacher") {
      const [loadedTests, loadedQuestions, loadedClasses] = await Promise.all([
        api("/tests"),
        api("/questions?page=1&page_size=20&approved=true"),
        api("/classes"),
      ]);
      if (version !== sessionVersion.current) return;
      setTests(loadedTests);
      setQuestions(loadedQuestions.items);
      setQuestionCount(loadedQuestions.approved_total ?? 0);
      setClasses(loadedClasses);
      await loadImports(version);
    } else {
      const [loadedTests, loadedClasses] = await Promise.all([
        api("/student/tests"),
        api("/student/classes"),
      ]);
      if (version !== sessionVersion.current) return;
      setTests(loadedTests);
      setStudentClasses(loadedClasses);
    }
  };
  useEffect(() => {
    load().catch(() => {});
  }, []);
  useEffect(() => {
    if (
      user?.role !== "teacher" ||
      !jobs.some(
        (job) => job.status === "uploaded" || job.status === "processing",
      )
    )
      return;
    const timer = window.setInterval(() => loadImports().catch(() => {}), 3000);
    return () => window.clearInterval(timer);
  }, [jobs, user?.role]);
  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 5000);
    return () => window.clearTimeout(timer);
  }, [message]);
  const login = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoginError("");
    try {
      const x = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(e.currentTarget))),
      });
      await load(x);
    } catch (e: any) {
      setLoginError(
        e.status === 401
          ? "Incorrect access code. Check the five digits and try again."
          : e.message,
      );
    }
  };
  if (!user && showLanding) return <LandingPage onSignIn={() => setShowLanding(false)} />;
  if (!user)
    return (
      <main className="auth">
        <div className="brand auth-brand">
          <button className="auth-back" onClick={() => setShowLanding(true)}>← Back to TestNow</button>
          <b>TestNow</b>
          <span>SECURE CBT DELIVERY</span>
          <h1>Assessment, delivered with confidence.</h1>
          <p>Choose your portal and use the access code issued to you.</p>
        </div>
        <Card>
          <h2>Sign in</h2>
          <form onSubmit={login} className="stacked-form">
            <div>
              <label htmlFor="login-role">I am a</label>
              <select id="login-role" name="role" defaultValue="student">
                <option value="student">Student</option>
                <option value="teacher">Teacher</option>
              </select>
            </div>
            <div>
              <label htmlFor="login-email">Email</label>
              <input id="login-email" name="email" type="email" autoComplete="email" required />
            </div>
            <div>
              <label htmlFor="login-access-code">5-digit access code</label>
              <input
                id="login-access-code"
                name="access_code"
                inputMode="numeric"
                pattern="[0-9]{5}"
                maxLength={5}
                autoComplete="one-time-code"
                aria-invalid={Boolean(loginError)}
                aria-describedby={loginError ? "login-access-code-error" : undefined}
                onChange={() => setLoginError("")}
                required
              />
              {loginError && <Notice kind="error"><span id="login-access-code-error">{loginError}</span></Notice>}
            </div>
            <button>Continue</button>
          </form>
          <small>
            For local demo data, use the documented test accounts in the project
            README.
          </small>
        </Card>
      </main>
    );
  const logout = async () => {
    sessionVersion.current += 1;
    await api("/auth/logout", { method: "POST" });
    setUser(null);
    setView("home");
    setShowLanding(true);
  };
  const openTest = (id: string, assignment = false) => {
    setSelectedTestId(id);
    setOpenAssignmentAfterCreate(assignment);
    setView("test-detail");
  };
  const queueResponseUpdate = (qid: string, answer: any, marked: boolean) => {
    const queued = responseSaveQueue.current.then(async () => {
      const current = attemptRef.current;
      if (!current) return false;
      try {
        const saved = await api(`/attempts/${current.id}/responses/${qid}`, {
          method: "PUT",
          body: JSON.stringify({ answer, marked_for_review: marked, revision: current.revision }),
        });
        const next = {
          ...current,
          revision: saved.revision,
          responses: { ...current.responses, [qid]: { answer, marked_for_review: marked } },
        };
        attemptRef.current = next;
        setAttempt(next);
        failedResponseSaves.current.delete(qid);
        return true;
      } catch (e: any) {
        failedResponseSaves.current.add(qid);
        setMessage(e.message);
        return false;
      }
    });
    responseSaveQueue.current = queued;
    return queued;
  };
  const finishAttempt = async () => {
    const current = attemptRef.current;
    if (!current || finishingAttempt.current === current.id) return false;
    finishingAttempt.current = current.id;
    try {
      await responseSaveQueue.current;
      const latest = attemptRef.current;
      const expired = Date.now() >= new Date(latest.ends_at).getTime();
      if (failedResponseSaves.current.size && !expired) {
        throw new Error("One or more answers could not be saved. Review the highlighted response and submit again.");
      }
      const submitted = await api(`/attempts/${latest.id}/submit`, {
        method: "POST",
        body: JSON.stringify({ revision: latest.revision }),
      });
      attemptRef.current = submitted;
      setAttempt(submitted);
      setView("result");
      return true;
    } catch (e: any) {
      finishingAttempt.current = null;
      setMessage(e.message);
      return false;
    }
  };
  if (pendingTest && !attempt)
    return (
      <ExamStartGate
        test={pendingTest}
        cancel={() => setPendingTest(null)}
        start={async () => {
          try {
            const created = await api(`/tests/${pendingTest.id}/attempts`, { method: "POST" });
            setPendingTest(null);
            setAttempt(created);
            setIndex(0);
            setView("exam");
          } catch (e: any) {
            setMessage(e.message);
            throw e;
          }
        }}
      />
    );
  if (user.role!=="admin" && typeof window!=="undefined" && window.location.pathname.startsWith("/admin/")) return <main className="page"><h1>Administrator access required</h1><p>This support workspace is restricted to administrators.</p><a href="/">Return to your dashboard</a></main>;
  return (
    <main className="application-shell">
      <AppConfirmHost />
      {view !== "exam" && (
        <WorkspaceSidebar
          user={user}
          view={view}
          navigate={(next) => {
            if (next === "builder") setBuilderImportId(null);
            if (next === "bank-import") {
              if (view === "teacher-detail") { window.location.assign("/admin/banks"); return; }
              window.history.pushState({}, "", "/admin/banks");
            }
            setView(next);
          }}
          home={() => { if (user.role === "admin") window.location.assign("/"); else setView("home"); }}
          logout={logout}
        />
      )}
      <section className={view === "exam" ? "exam-shell" : "workspace-content"}>
      {message && <div className="toast">{message}</div>}
      {view === "home" &&
        (user.role === "student" ? (
          <StudentHome
            user={user}
            tests={tests}
            classes={studentClasses}
            start={async (id) => {
              const test = tests.find((item) => item.id === id);
              if (test) setPendingTest(test);
            }}
            viewResult={(id) => {
              setAttempt({ id });
              setView("result");
            }}
          />
        ) : user.role === "admin" ? (
          <AdminHome
            dashboard={admin}
            refresh={() => load(user)}
            notify={setMessage}
          />
        ) : (
          <TeacherHome
            tests={tests}
            questionCount={questionCount}
            classes={classes}
            jobs={jobs}
            onTestDeleted={(id) =>
              setTests((current) => current.filter((test) => test.id !== id))
            }
            openImport={(id) => {
              setSelectedJobId(id);
              setView("reviews");
            }}
            openTest={openTest}
            refresh={() => load(user)}
          />
        ))}
      {view === "classes" && (
        <ClassesScreen
          classes={classes}
          refresh={async () => {
            setClasses(await api("/classes"));
          }}
          notify={setMessage}
        />
      )}
      {view === "import" && (
        <ImportScreen
          onDone={async (job) => {
            setSelectedJobId(job.id);
            setJobs((current) => [job, ...current]);
            previousJobs.current.set(job.id, job.status);
            setView("reviews");
            setMessage(
              "Import queued. Track it in the dedicated paper review workspace.",
            );
          }}
          job={null}
          jobs={jobs}
          selectJob={setSelectedJobId}
          openReview={(id) => {
            setSelectedJobId(id);
            setView("reviews");
          }}
          onDeleted={(id) => {
            setJobs((current) => current.filter((item) => item.id !== id));
            setMessage("Import deleted.");
          }}
          refresh={async () => {}}
        />
      )}
      {view === "reviews" && (
        <ImportScreen
          reviewOnly
          onDone={() => {}}
          job={jobs.find((job) => job.id === selectedJobId) || null}
          jobs={jobs}
          selectJob={setSelectedJobId}
          createTestFromImport={(id) => {
            setBuilderImportId(id);
            setView("builder");
          }}
          onDeleted={(id) => {
            const remaining = jobs.filter((item) => item.id !== id);
            setJobs(remaining);
            setSelectedJobId(remaining[0]?.id || null);
            setMessage("Import deleted.");
          }}
          refresh={async () => {
            if (!selectedJobId) return;
            const updated = await api(`/teacher/imports/${selectedJobId}`);
            setJobs((current) =>
              current.map((job) => (job.id === updated.id ? updated : job)),
            );
            previousJobs.current.set(updated.id, updated.status);
          }}
        />
      )}
      {view === "bank-import" && <QuestionBankManager notify={setMessage} />}
      {view === "teacher-detail" && user.role === "admin" && supportTeacher && <TeacherSupport teacherId={supportTeacher} api={api}/>}
      {view === "builder" && (
        <Builder
          initialQuestions={questions}
          initialQuestionCount={questionCount}
          imports={jobs}
          initialImportId={builderImportId}
          onDone={async (id, publishedFromImport) => {
            setBuilderImportId(null);
            openTest(id, publishedFromImport);
            setMessage(publishedFromImport
              ? "Test created and published. Assign it to a class now."
              : "Draft created. Add classes and publish it when ready.");
            // Creating the draft already succeeded before this callback runs.
            // Refresh the dashboard in the background so a stale or malformed
            // question in the library cannot turn that successful operation
            // into a misleading "Request failed" message.
            void load(user).catch(() => {});
          }}
        />
      )}
      {view === "test-detail" && selectedTestId && (
        <TestDetail
          testId={selectedTestId}
          initialTab={openAssignmentAfterCreate ? "assignment" : "preview"}
          allClasses={classes}
          back={() => setView("home")}
          refresh={() => load(user)}
          notify={setMessage}
        />
      )}
      {view === "exam" && attempt && (
        <Exam
          attempt={attempt}
          index={index}
          setIndex={setIndex}
          cancelForIntegrity={async (eventType, clientTime) => {
            const cancelled = await api(
              `/attempts/${attempt.id}/integrity-violation`,
              {
                method: "POST",
                keepalive: true,
                body: JSON.stringify({
                  event_type: eventType,
                  client_time: clientTime,
                }),
              },
            );
            setAttempt(cancelled);
            return cancelled;
          }}
          returnHome={async () => {
            setView("home");
            await load(user);
          }}
          finish={finishAttempt}
          update={queueResponseUpdate}
        />
      )}
      {view === "result" && attempt && <Result attempt={attempt} />}
      </section>
    </main>
  );
}

function WorkspaceSidebar({ user, view, navigate, home, logout }: {
  user: User;
  view: WorkspaceView;
  navigate: (view: WorkspaceView) => void;
  home: () => void;
  logout: () => void;
}) {
  const teacherItems: { view: WorkspaceView; label: string; detail: string }[] = [
    { view: "home", label: "Dashboard", detail: "Your teaching overview" },
    { view: "classes", label: "Classes & batches", detail: "Students and rosters" },
    { view: "import", label: "Import papers", detail: "Add source material" },
    { view: "reviews", label: "Review papers", detail: "Prepare questions" },
    { view: "builder", label: "Build assessment", detail: "Create and assign tests" },
  ];
  const adminItems: { view: WorkspaceView; label: string; detail: string }[] = [
    { view: "home", label: "Admin dashboard", detail: "Platform overview" },
    { view: "bank-import", label: "Question banks", detail: "Manage shared content" },
  ];
  const studentItems: { view: WorkspaceView; label: string; detail: string }[] = [
    { view: "home", label: "My assessments", detail: "Upcoming and completed tests" },
  ];
  const items = user.role === "teacher" ? teacherItems : user.role === "admin" ? adminItems : studentItems;
  const initials = user.name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "TN";
  return <aside className="workspace-sidebar" aria-label="Application navigation">
    <button className="workspace-brand" onClick={home} aria-label="TestNow dashboard">Test<span>Now</span></button>
    <div className="workspace-context"><span className="workspace-avatar" aria-hidden="true">{initials}</span><div><b>{user.name}</b><small>{user.role === "teacher" ? "Teacher workspace" : user.role === "admin" ? "Administrator" : "Student workspace"}</small></div></div>
    <nav className="workspace-nav" aria-label="Workspace sections">
      <p>Workspace</p>
      {items.map((item) => <button type="button" key={item.view} className={view === item.view ? "active" : ""} onClick={() => navigate(item.view)}><b>{item.label}</b><small>{item.detail}</small></button>)}
    </nav>
    <div className="workspace-sidebar-footer"><button type="button" className="sidebar-signout" onClick={logout}>Sign out</button></div>
  </aside>;
}

function ImportSummary({ job, open }: { job: ImportJob; open: () => void }) {
  const labels: Record<ImportJob["status"], string> = {
    uploaded: "Queued",
    processing: "Processing",
    review: "Needs review",
    failed: "Needs attention",
    approved: "Review complete",
  };
  return (
    <article className="row import-row">
      <div>
        <b>
          {job.meta?.paper_title || job.filename || "Question paper import"}
        </b>
        <small>
          {job.meta?.phase || labels[job.status]}
          {job.meta?.question_count !== undefined
            ? ` · ${job.meta.question_count} questions found`
            : ""}
        </small>
      </div>
      <div>
        <span className={`badge ${job.status}`}>{labels[job.status]}</span>
        <button onClick={open}>
          {job.status === "review" ? "Review" : "View progress"}
        </button>
      </div>
    </article>
  );
}
function OverviewChart({title, description, rows}: {title:string; description:string; rows:{label:string;value:number}[]}) {
  const maximum = Math.max(1, ...rows.map(row => row.value));
  return <section className="card overview-chart"><h2>{title}</h2><p>{description}</p>{rows.length ? <div className="overview-chart-rows">{rows.map((row,index) => <div className="overview-chart-row" key={`${row.label}-${index}`}><span>{row.label}</span><meter min={0} max={maximum} value={row.value} aria-label={`${row.label}: ${row.value}`}/><b>{row.value.toLocaleString()}</b></div>)}</div> : <p>No data yet. Your activity will appear here.</p>}</section>;
}

function TeacherHome({
  tests,
  questionCount,
  classes,
  jobs,
  onTestDeleted,
  openImport,
  openTest,
  refresh,
}: {
  tests: Test[];
  questionCount: number;
  classes: Classroom[];
  jobs: ImportJob[];
  onTestDeleted: (id: string) => void;
  openImport: (id: string) => void;
  openTest: (id: string) => void;
  refresh: () => Promise<void> | void;
}) {
  const actionable = jobs.filter((job) => job.status !== "approved"),
    students = classes.reduce((total, item) => total + item.student_count, 0),
    published = tests.filter((test) => test.status === "published").length;
  const [error, setError] = useState("");
  const deleteTest = async (test: Test) => {
    if (!await confirmApp(`Delete “${test.title}”? This cannot be undone.`)) return;
    try {
      await api(`/tests/${test.id}`, { method: "DELETE" });
      onTestDeleted(test.id);
      // The delete is already confirmed. Refresh in the background so an
      // unrelated question-library failure cannot report it as unsuccessful.
      void Promise.resolve(refresh()).catch(() => {});
    } catch (e: any) {
      setError(e.message);
    }
  };
  return (
    <div className="page">
      <p className="eyebrow">TEACHER WORKSPACE</p>
      <h1>Assessment control centre</h1>
      <div className="role-overview">
        <OverviewChart title="Assessment pipeline" description="Current assessments by publication status" rows={['draft','published','archived'].map(status=>({label:status,value:tests.filter(test=>test.status===status).length}))}/>
        <OverviewChart title="Class enrolment" description="Student enrolments across your classes and batches" rows={classes.map(item=>({label:item.name,value:item.student_count}))}/>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="stats">
        <Card>
          <b>{classes.length}</b>
          <span>Classes / batches</span>
        </Card>
        <Card>
          <b>{students}</b>
          <span>Student enrolments</span>
        </Card>
        <Card>
          <b>{published}</b>
          <span>Published tests</span>
        </Card>
        <Card>
          <b>{questionCount}</b>
          <span>Approved questions</span>
        </Card>
      </div>
      {actionable.length > 0 && (
        <Card>
          <h2>Import notifications</h2>
          <p>
            These imports remain visible after refresh and can be reopened any
            time.
          </p>
          {actionable.map((job) => (
            <ImportSummary
              key={job.id}
              job={job}
              open={() => openImport(job.id)}
            />
          ))}
        </Card>
      )}
      <Card>
        <h2>Your assessments</h2>
        {tests.length ? (
          tests.map((t) => (
            <article className="row" key={t.id}>
              <div>
                <b>{t.title}</b>
                <small>
                  {t.subject} · {t.question_count} questions ·{" "}
                  {Math.ceil(t.duration_seconds / 60)} min ·{" "}
                  {t.assigned_class_names?.length
                    ? `Assigned to: ${t.assigned_class_names.join(", ")} · ${t.assigned_student_count} students`
                    : "Not assigned to a batch"}
                </small>
              </div>
              <div>
                <span className={`badge ${t.status}`}>{t.status}</span>
                <button
                  className="secondary-action"
                  onClick={() => openTest(t.id)}
                >
                  View
                </button>
                <button
                  type="button"
                  className="text-danger"
                  onClick={() => deleteTest(t)}
                >
                  Delete
                </button>
                {t.status === "draft" && (
                  <button
                    onClick={async () => {
                      await api(`/tests/${t.id}/publish`, { method: "POST" });
                      await refresh();
                    }}
                  >
                    Publish
                  </button>
                )}
              </div>
            </article>
          ))
        ) : (
          <p>
            No assessments yet. Build a test from approved questions to begin.
          </p>
        )}
      </Card>
    </div>
  );
}

function AdminHome({
  dashboard,
  refresh,
  notify,
}: {
  dashboard: AdminDashboard | null;
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    setBusy(true);
    setError("");
    try {
      await api("/admin/teachers", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.reset();
      await refresh();
      notify("Teacher created and access code issued.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = async (teacher: AdminDashboard["teachers"][number]) => {
    if(!await confirmApp(`${teacher.status==="suspended"?"Reactivate":"Suspend"} ${teacher.name} (${teacher.email})? ${teacher.status==="suspended"?"Sign-in will be restored.":"Sign-in will be blocked and existing sessions revoked."} Tests and attempts are preserved.`))return;
    try {
      await api(`/admin/teachers/${teacher.id}`, {
        method: "PUT",
        body: JSON.stringify({
          status: teacher.status === "suspended" ? "active" : "suspended",
        }),
      });
      await refresh();
      notify(
        `Teacher ${teacher.status === "suspended" ? "reactivated" : "suspended"}.`,
      );
    } catch (e: any) {
      setError(e.message);
    }
  };
  if (!dashboard)
    return <div className="page">Loading administrator dashboard…</div>;
  return (
    <div className="page">
      <p className="eyebrow">SUPER ADMIN</p>
      <h1>Teacher and platform management</h1>
      <div className="role-overview">
        <OverviewChart title="Teacher access" description="Current account status across the platform" rows={[{label:'Active',value:dashboard.summary.active_teachers},{label:'Suspended',value:dashboard.summary.suspended_teachers},{label:'Awaiting access',value:Math.max(0,dashboard.summary.teachers-dashboard.summary.active_teachers-dashboard.summary.suspended_teachers)}]}/>
        <OverviewChart title="Assessment participation" description="Attempts recorded across all teacher accounts" rows={[{label:'Total attempts',value:dashboard.teachers.reduce((sum,item)=>sum+item.attempt_count,0)},{label:'Submitted',value:dashboard.teachers.reduce((sum,item)=>sum+item.submitted_count,0)},{label:'Assessments',value:dashboard.teachers.reduce((sum,item)=>sum+item.test_count,0)}]}/>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="stats">
        <Card>
          <b>{dashboard.summary.teachers}</b>
          <span>Teachers</span>
        </Card>
        <Card>
          <b>{dashboard.summary.active_teachers}</b>
          <span>Active teachers</span>
        </Card>
        <Card>
          <b>{dashboard.summary.suspended_teachers}</b>
          <span>Suspended</span>
        </Card>
        <Card>
          <b>{dashboard.summary.students}</b>
          <span>Students</span>
        </Card>
      </div>
      <Card>
        <h2>Add teacher</h2>
        <form onSubmit={create} className="stacked-form compact-form">
          <div>
            <label htmlFor="teacher-name">Name</label>
            <input id="teacher-name" name="name" autoComplete="name" required />
          </div>
          <div>
            <label htmlFor="teacher-email">Email</label>
            <input id="teacher-email" name="email" type="email" autoComplete="email" required />
          </div>
          <div>
            <label htmlFor="teacher-access-code">5-digit access code</label>
            <input
              id="teacher-access-code"
              name="access_code"
              autoComplete="off"
              inputMode="numeric"
              pattern="[0-9]{5}"
              maxLength={5}
              required
            />
          </div>
          <button disabled={busy}>
            {busy ? "Creating…" : "Create teacher"}
          </button>
        </form>
      </Card>
      <Card>
        <h2>Teacher activity</h2>
        {dashboard.teachers.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Teacher</th>
                  <th>Status</th>
                  <th>Last login</th>
                  <th>Batches</th>
                  <th>Students</th>
                  <th>Tests</th>
                  <th>Completed attempts</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {dashboard.teachers.map((teacher) => (
                  <tr key={teacher.id}>
                    <td>
                      <a href={`/admin/teachers/${encodeURIComponent(teacher.id)}`}>{teacher.name}</a>
                      <small>{teacher.email}</small>
                    </td>
                    <td>
                      <span className={`badge ${teacher.status}`}>
                        {teacher.status}
                      </span>
                    </td>
                    <td>{fmtDate(teacher.last_login_at)}</td>
                    <td>{teacher.batch_count}</td>
                    <td>{teacher.student_count}</td>
                    <td>{teacher.test_count}</td>
                    <td>{teacher.submitted_count}</td>
                    <td>
                      <button
                        className="secondary-action"
                        onClick={() => toggle(teacher)}
                      >
                        {teacher.status === "suspended"
                          ? "Reactivate"
                          : "Suspend"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>No teachers have been created.</p>
        )}
      </Card>
    </div>
  );
}

function ClassesScreen({
  classes,
  refresh,
  notify,
}: {
  classes: Classroom[];
  refresh: () => Promise<void>;
  notify: (message: string) => void;
}) {
  const [selected, setSelected] = useState<Classroom | null>(null),
    [detail, setDetail] = useState<Classroom | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const open = async (id: string) => {
    setError("");
    try {
      const value = await api(`/classes/${id}`);
      setSelected(value);
      setDetail(value);
    } catch (e: any) {
      setError(e.message);
    }
  };
  useEffect(() => {
    if (!selected && classes[0]) open(classes[0].id);
  }, [classes]);
  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    setBusy(true);
    try {
      const value = await api("/classes", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      await refresh();
      await open(value.id);
      form.reset();
      notify("Class / batch created.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const addStudent = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!detail) return;
    setBusy(true);
    try {
      const data = Object.fromEntries(new FormData(form));
      await api("/students", {
        method: "POST",
        body: JSON.stringify({ ...data, class_id: detail.id }),
      });
      await open(detail.id);
      await refresh();
      form.reset();
      notify("Student saved and enrolled.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const importCsv = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!detail) return;
    setBusy(true);
    try {
      const file = new FormData(e.currentTarget);
      const result = await fetch(`/api/classes/${detail.id}/students/import`, {
        method: "POST",
        credentials: "include",
        body: file,
      });
      if (!result.ok) throw new Error((await result.json()).detail);
      const report = await result.json();
      await open(detail.id);
      await refresh();
      notify(
        `Imported ${report.created} new, updated ${report.updated}; ${report.errors.length} rows skipped.`,
      );
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <p className="eyebrow">CLASS / BATCH MANAGEMENT</p>
      <h1>Classes, batches and student rosters</h1>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="class-layout">
        <aside className="class-list">
          <Card>
            <h2>Create class / batch</h2>
            <form onSubmit={create} className="stacked-form">
              <div>
                <label htmlFor="class-name">Name</label>
                <input id="class-name" name="name" placeholder="e.g. Physics 12A" required />
              </div>
              <div>
                <label>
                  Description <span className="optional">(optional)</span>
                </label>
                <input name="description" placeholder="Term or section" />
              </div>
              <button disabled={busy}>Create</button>
            </form>
          </Card>
          <Card>
            <h2>Your groups</h2>
            {classes.length ? (
              classes.map((item) => (
                <button
                  key={item.id}
                  className={`class-choice ${selected?.id === item.id ? "selected" : ""}`}
                  onClick={() => open(item.id)}
                >
                  <b>{item.name}</b>
                  <small>{item.student_count} students</small>
                </button>
              ))
            ) : (
              <p>No groups yet.</p>
            )}
          </Card>
        </aside>
        <section>
          {detail ? (
            <Card className="roster-card">
              <div className="section-heading">
                <div>
                  <h2>{detail.name}</h2>
                  <p>{detail.description || "No description provided."}</p>
                </div>
                <span className="badge published">
                  {detail.student_count} students
                </span>
              </div>
              <form onSubmit={addStudent} className="stacked-form compact-form">
                <h3>Add or update student</h3>
                <div>
                  <label htmlFor="student-name">Name</label>
                  <input id="student-name" name="name" required />
                </div>
                <div>
                  <label htmlFor="student-email">Email</label>
                  <input id="student-email" name="email" type="email" required />
                </div>
                <div>
                  <label htmlFor="student-access-code">5-digit access code</label>
                  <input
                    id="student-access-code"
                    name="access_code"
                    inputMode="numeric"
                    pattern="[0-9]{5}"
                    maxLength={5}
                    required
                  />
                </div>
                <button disabled={busy}>Save student</button>
              </form>
              <form onSubmit={importCsv} className="stacked-form compact-form">
                <h3>Import CSV</h3>
                <div>
                  <label>CSV: name, email, access_code</label>
                  <input
                    name="file"
                    type="file"
                    accept=".csv,text/csv"
                    required
                  />
                </div>
                <button className="secondary-action" disabled={busy}>
                  Import students
                </button>
              </form>
              <div className="member-list">
                <h3>Students</h3>
                {detail.students?.length ? (
                  detail.students.map((student) => (
                    <article className="row" key={student.id}>
                      <div>
                        <b>{student.name}</b>
                        <small>{student.email}</small>
                      </div>
                      <button
                        className="text-danger"
                        onClick={async () => {
                          if (!detail) return;
                          if (!await confirmApp(`Remove ${student.name} from ${detail.name}? Their account and assessment history will be preserved.`)) return;
                          await api(
                            `/classes/${detail.id}/members/${student.id}`,
                            { method: "DELETE" },
                          );
                          await open(detail.id);
                          await refresh();
                          notify("Student removed safely from group.");
                        }}
                      >
                        Remove
                      </button>
                    </article>
                  ))
                ) : (
                  <p>No students in this group.</p>
                )}
              </div>
            </Card>
          ) : (
            <Card>Select a group to manage its roster.</Card>
          )}
        </section>
      </div>
    </div>
  );
}

type ExplainerRevision = { id: string; question_id: string; status: string; progress_message?: string; feedback?: string; error?: string; created_at: string; published_at?: string; preview_url?: string };
type ExplainerList = { questions: { id: string; source_number?: string; stem_markdown: string; has_solution: boolean; suggested: boolean }[]; revisions: ExplainerRevision[] };
const explainerStages = ["queued", "planning", "narrating", "rendering", "finalizing"];
const explainerStageLabels: Record<string, string> = { queued: "Formulating", planning: "Formulating", narrating: "Narrating", rendering: "Building", finalizing: "Finalizing", ready: "Ready", failed: "Failed", cancelled: "Cancelled" };
const explainerAnimatedStages = ["Formulating", "Narrating", "Building", "Finalizing"];
const explainerStageIndex: Record<string, number> = { queued: 0, planning: 0, narrating: 1, rendering: 2, finalizing: 3 };

function ExplainerRevisionDetails({ row, questionLabel, busy, publish, cancel }: { row: ExplainerRevision; questionLabel: string; busy: string; publish: (revisionId: string) => Promise<void>; cancel: (revisionId: string) => Promise<void> }) {
  const active = explainerStages.includes(row.status);
  return <>
    <div className="explainer-revision-meta"><b>{row.published_at ? "Published" : "Draft"}</b><span className={`badge ${row.status}`}>{active ? "In progress" : explainerStageLabels[row.status] || row.status}</span><small>{fmtDate(row.created_at)}</small></div>
    {active && <div className="explainer-progress">
      <div className="explainer-stage-window" aria-hidden="true"><div className="explainer-stage-track" style={{ transform: `translateY(-${explainerStageIndex[row.status] * 2.5}rem)` }}>{explainerAnimatedStages.map(stage => <div className="explainer-stage-item" key={stage}><span>{stage}</span><span className="explainer-dots"><i /><i /><i /></span></div>)}</div></div>
      <span className="explainer-status-accessible" role="status" aria-live="polite">{explainerStageLabels[row.status]}. {row.progress_message || "Generation in progress"}</span>
      {row.progress_message && row.status !== "queued" && <p className="explainer-stage-detail">{row.progress_message}</p>}
      <button type="button" className="secondary-action" disabled={Boolean(busy)} onClick={() => void cancel(row.id)}>{busy === row.id ? "Cancelling…" : "Cancel generation"}</button>
    </div>}
    {row.status === "cancelled" && <p>Generation cancelled.</p>}
    {row.feedback && <p>Feedback: {row.feedback}</p>}
    {row.error && <p role="alert">Generation failed: {row.error}</p>}
    {row.preview_url && <><video controls preload="metadata" src={row.preview_url} aria-label={`Explainer preview for ${questionLabel}`} /><button className="secondary-action" disabled={Boolean(busy) || Boolean(row.published_at)} onClick={() => void publish(row.id)}>{row.published_at ? "Published to students" : "Publish this version"}</button></>}
  </>;
}

function ExplainerHistoryDialog({ question, index, revisions, busy, publish, cancel, close }: { question: ExplainerList["questions"][number]; index: number; revisions: ExplainerRevision[]; busy: string; publish: (revisionId: string) => Promise<void>; cancel: (revisionId: string) => Promise<void>; close: () => void }) {
  const dialogFocus = useDialogFocus(true, close, () => document.querySelector<HTMLButtonElement>(`[data-explainer-history="${question.id}"]`));
  const questionLabel = `question ${question.source_number || index + 1}`;
  return <div className="explainer-history-backdrop" role="dialog" aria-modal="true" aria-labelledby={`explainer-history-title-${question.id}`} ref={dialogFocus} tabIndex={-1}>
    <section className="explainer-history-dialog">
      <div className="explainer-history-heading"><div><p className="eyebrow">GENERATION HISTORY</p><h2 id={`explainer-history-title-${question.id}`}>Question {question.source_number || index + 1}</h2><p>{revisions.length} generation{revisions.length === 1 ? "" : "s"}, newest first.</p></div><button type="button" className="secondary-action" onClick={close}>Close history</button></div>
      <div className="explainer-history-list">{revisions.map((row, revisionIndex) => <article className="explainer-revision" key={row.id}><p className="explainer-version-label">{revisionIndex === 0 ? "Latest version" : `Version ${revisions.length - revisionIndex}`}</p><ExplainerRevisionDetails row={row} questionLabel={questionLabel} busy={busy} publish={publish} cancel={cancel} /></article>)}</div>
    </section>
  </div>;
}

function TestExplainers({ test }: { test: Test }) {
  const [data, setData] = useState<ExplainerList | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState<Record<string, string>>({});
  const [historyQuestionId, setHistoryQuestionId] = useState<string | null>(null);
  const load = async () => { try { setData(await api(`/tests/${test.id}/explainers`)); setError(""); } catch (e: any) { setError(e.message); } };
  useEffect(() => { void load(); }, [test.id, test.status]);
  useEffect(() => {
    if (!data?.revisions.some(row => explainerStages.includes(row.status))) return;
    const timer = window.setInterval(() => { void load(); }, 3000);
    return () => window.clearInterval(timer);
  }, [data, test.id]);
  const generate = async (questionId: string, override: boolean) => {
    setBusy(questionId); setError("");
    try {
      await api(`/tests/${test.id}/explainers/${questionId}/generate`, { method: "POST", body: JSON.stringify({ feedback: feedback[questionId] || "", override_suggestion: override }) });
      setFeedback(current => ({ ...current, [questionId]: "" }));
      await load();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(""); }
  };
  const publish = async (revisionId: string) => {
    setBusy(revisionId); setError("");
    try { await api(`/tests/${test.id}/explainers/${revisionId}/publish`, { method: "POST" }); await load(); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(""); }
  };
  const cancel = async (revisionId: string) => {
    setBusy(revisionId); setError("");
    try { await api(`/tests/${test.id}/explainers/${revisionId}/cancel`, { method: "POST" }); await load(); }
    catch (e: any) { await load(); setError(e.message); }
    finally { setBusy(""); }
  };
  const historyQuestionIndex = data?.questions.findIndex(question => question.id === historyQuestionId) ?? -1;
  const historyQuestion = historyQuestionIndex >= 0 ? data?.questions[historyQuestionIndex] : null;
  const historyRevisions = historyQuestion ? data?.revisions.filter(row => row.question_id === historyQuestion.id) || [] : [];
  return <><Card className="explainer-workspace">
    <div className="explainer-heading"><div><h2>Question explainers</h2><p>Generate a short visual lesson, review it, then publish the version students should see with their results.</p></div><button className="secondary-action" onClick={() => void load()}>Refresh</button></div>
    {error && <Notice kind="error">{error}</Notice>}
    {!data ? <p>Loading explainers…</p> : !data.questions.length ? <p>Publish this assessment to generate explainers from its saved questions.</p> : data.questions.map((question, index) => {
      const revisions = data.revisions.filter(row => row.question_id === question.id);
      const active = revisions.some(row => explainerStages.includes(row.status));
      const latest = revisions[0];
      const published = revisions.some(row => Boolean(row.published_at));
      const limitReached = revisions.length >= 3;
      return <section className="explainer-question" key={question.id}>
        <div className="explainer-question-heading"><div><h3>Question {question.source_number || index + 1}</h3><Markdown value={question.stem_markdown} className="explainer-stem" />
          <small>{!question.has_solution ? "Needs a worked solution" : question.suggested ? "Suggested for animation" : "Animation optional"}</small></div>
          {revisions.length > 0 && <button type="button" className="explainer-history-button" data-explainer-history={question.id} onClick={() => setHistoryQuestionId(question.id)} aria-label={`View ${revisions.length} generation${revisions.length === 1 ? "" : "s"} for question ${question.source_number || index + 1}`} title="View generation history"><span aria-hidden="true">↶</span><span>{revisions.length}</span></button>}</div>
        {question.has_solution && !published && !limitReached && <div className="explainer-controls">
          <label htmlFor={`explainer-feedback-${question.id}`}>Directions for the next draft</label>
          <textarea id={`explainer-feedback-${question.id}`} rows={2} maxLength={2000} value={feedback[question.id] || ""} onChange={event => setFeedback(current => ({ ...current, [question.id]: event.target.value }))} placeholder="For example: slow down the graph reveal and explain the sign change." />
          <button disabled={active || Boolean(busy)} onClick={() => void generate(question.id, !question.suggested)}>{active ? "Generating…" : revisions.length ? "Generate new draft" : "Generate explainer"}</button>
        </div>}
        {!published && limitReached && <p>Maximum of 3 explainer drafts reached.</p>}
        {latest && <div className="explainer-revision"><p className="explainer-version-label">Latest version</p><ExplainerRevisionDetails row={latest} questionLabel={`question ${question.source_number || index + 1}`} busy={busy} publish={publish} cancel={cancel} /></div>}
      </section>;
    })}
  </Card>{historyQuestion && <ExplainerHistoryDialog question={historyQuestion} index={historyQuestionIndex} revisions={historyRevisions} busy={busy} publish={publish} cancel={cancel} close={() => setHistoryQuestionId(null)} />}</>;
}

function TestDetail({
  testId,
  initialTab,
  allClasses,
  back,
  refresh,
  notify,
}: {
  testId: string;
  initialTab?: "preview" | "assignment" | "analytics";
  allClasses: Classroom[];
  back: () => void;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [test, setTest] = useState<Test | null>(null),
    [analytics, setAnalytics] = useState<Analytics | null>(null),
    [tab, setTab] = useState<"preview" | "assignment" | "analytics" | "explainers">(initialTab || "preview"),
    [error, setError] = useState(""),
    [publishing, setPublishing] = useState(false),
    [savingAssignments, setSavingAssignments] = useState(false),
    [deleting, setDeleting] = useState(false);
  const load = async () => {
    try {
      setTest(await api(`/tests/${testId}`));
    } catch (e: any) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
  }, [testId]);
  if (!test) return <div className="page">Loading assessment…</div>;
  const assigned = new Set(test.classes?.map((c) => c.id) || []);
  const saveClasses = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSavingAssignments(true);
    setError("");
    try {
      const form = new FormData(e.currentTarget);
      const class_ids = form.getAll("class_id").map(String);
      const updated = await api(`/tests/${test.id}/classes`, {
        method: "PUT",
        body: JSON.stringify({ class_ids }),
      });
      setTest(updated);
      notify("Class assignments saved.");
      // The assignment is already persisted. Keep the dashboard refresh
      // independent so an unrelated library refresh cannot make this form
      // look as though it is still saving or failed.
      void refresh().catch(() => {});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSavingAssignments(false);
    }
  };
  const loadAnalytics = async () => {
    setTab("analytics");
    try {
      setAnalytics(await api(`/tests/${test.id}/analytics`));
    } catch (e: any) {
      setError(e.message);
    }
  };
  const deleteCurrentTest = async () => {
    if (!await confirmApp(`Delete “${test.title}”? This cannot be undone.`)) return;
    setDeleting(true);
    setError("");
    try {
      await api(`/tests/${test.id}`, { method: "DELETE" });
      await refresh();
      notify("Test deleted.");
      back();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDeleting(false);
    }
  };
  return (
    <div className="page test-detail">
      <button className="back-link" onClick={back}>
        ← Dashboard
      </button>
      <div className="detail-hero">
        <div>
          <p className="eyebrow">ASSESSMENT DETAIL</p>
          <h1>{test.title}</h1>
          <p>
            {test.subject} · {test.question_count} questions ·{" "}
            {Math.ceil(test.duration_seconds / 60)} minutes
          </p>
          <p>
            {test.assigned_class_names?.length
              ? `Assigned to: ${test.assigned_class_names.join(", ")}`
              : "Not assigned to a batch yet"}
          </p>
        </div>
        <div className="detail-actions">
          <span className={`badge ${test.status}`}>{test.status}</span>
          {test.status === "draft" && (
            <button
              onClick={async () => {
                setPublishing(true);
                setError("");
                try {
                  await api(`/tests/${test.id}/publish`, { method: "POST" });
                  await load();
                  await refresh();
                  notify(
                    "Test published. Assign a class whenever you are ready.",
                  );
                } catch (e: any) {
                  setError(e.message);
                } finally {
                  setPublishing(false);
                }
              }}
              disabled={publishing}
            >
              Publish test
            </button>
          )}
          <button
            type="button"
            className="text-danger"
            onClick={deleteCurrentTest}
            disabled={deleting}
          >
            {deleting ? "Deleting…" : "Delete test"}
          </button>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      <nav className="tabs">
        <button
          className={tab === "preview" ? "active" : ""}
          onClick={() => setTab("preview")}
        >
          Preview
        </button>
        <button
          className={tab === "assignment" ? "active" : ""}
          onClick={() => setTab("assignment")}
        >
          {test.class_count ? `Assigned batches (${test.class_count})` : "No batch assigned"}
        </button>
        <button
          className={tab === "analytics" ? "active" : ""}
          onClick={loadAnalytics}
        >
          Analytics
        </button>
        <button className={tab === "explainers" ? "active" : ""} onClick={() => setTab("explainers")}>Explainers</button>
      </nav>
      {tab === "explainers" && <TestExplainers test={test} />}
      {tab === "preview" && (
        <Card>
          <h2>Immutable test preview</h2>
          <p>
            Students receive this question snapshot when they start the
            assessment.
          </p>
          {(test.delivery_blueprint?.sections || [{ id: "general", title: "General", question_ids: (test.questions || []).map((q) => q.id) }]).map((section) => (
            <section className="test-preview-section" key={section.id}>
              <h3>{section.title}</h3>
              <p>{section.question_ids.length} {section.question_ids.length === 1 ? "question" : "questions"}</p>
              {section.question_ids.map((id) => {
                const q = test.questions?.find((question) => question.id === id);
                return q ? <QuestionCard key={q.id} q={q} mode="teacher" /> : null;
              })}
            </section>
          ))}
        </Card>
      )}
      {tab === "assignment" && (
        <Card>
          <h2>Assign classes</h2>
          <p>
            Publishing without a class is allowed. The test appears to students
            only after their class is selected here.
          </p>
          <form onSubmit={saveClasses} className="assignment-form">
            {allClasses.length ? (
              allClasses.map((item) => (
                <label key={item.id} className="assignment-choice">
                  <input
                    type="checkbox"
                    name="class_id"
                    value={item.id}
                    defaultChecked={assigned.has(item.id)}
                  />
                  <span>
                    <b>{item.name}</b>
                    <small>
                      {item.student_count} students ·{" "}
                      {item.description || "No description"}
                    </small>
                  </span>
                </label>
              ))
            ) : (
              <Notice>
                Create a class first, then return here to assign it.
              </Notice>
            )}
            <button disabled={savingAssignments}>
              {savingAssignments ? "Saving assignments…" : "Save assignments"}
            </button>
          </form>
        </Card>
      )}
      {tab === "analytics" && <AnalyticsPanel analytics={analytics} testId={test.id} />}
    </div>
  );
}
function AnalyticsPanel({ analytics, testId }: { analytics: Analytics | null; testId:string }) {
  if (!analytics) return <Card>Loading analytics…</Card>;
  return (
    <>
      <div className="stats analytics-stats">
        <Card>
          <b>{analytics.summary.assigned}</b>
          <span>Candidates</span>
          <small>{analytics.summary.currently_assigned} currently assigned</small>
        </Card>
        <Card>
          <b>{analytics.summary.started}</b>
          <span>Started</span>
        </Card>
        <Card>
          <b>{analytics.summary.submitted}</b>
          <span>Submitted</span>
        </Card>
        <Card>
          <b>{analytics.summary.pending}</b>
          <span>Not started</span>
        </Card>
        <Card>
          <b>{analytics.summary.average_percentage ?? "—"}{analytics.summary.average_percentage !== undefined && analytics.summary.average_percentage !== null ? "%" : ""}</b>
          <span>Average score</span>
        </Card>
        <Card className={analytics.summary.flagged ? "integrity-stat" : ""}>
          <b>{analytics.summary.flagged}</b>
          <span>Integrity flags</span>
        </Card>
      </div>
      <TeacherDiagnostics testId={testId} />
      <Card>
        <div className="section-heading">
          <div>
            <h2>Batch analytics</h2>
            <p>Compare participation, performance, and integrity by assigned batch.</p>
          </div>
        </div>
        {analytics.batches.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Batch</th><th>Assigned</th><th>Completed</th><th>Average</th><th>Cancelled</th><th>Flagged</th></tr></thead>
              <tbody>{analytics.batches.map((batch) => (
                <tr key={batch.classroom.id}>
                  <td><b>{batch.classroom.name}</b></td>
                  <td>{batch.assigned}</td>
                  <td>{batch.submitted} <small>{batch.completion_rate}% completion</small></td>
                  <td>{batch.average_percentage !== undefined && batch.average_percentage !== null ? `${batch.average_percentage}%` : "—"}</td>
                  <td>{batch.cancelled}</td>
                  <td><span className={`integrity-count ${batch.flagged ? "flagged" : ""}`}>{batch.flagged}</span></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <p>No batches are assigned to this exam.</p>}
      </Card>
      <Card>
        <div className="section-heading">
          <div>
            <h2>Student analytics</h2>
            <p>Open a student to inspect every attempt, response outcome, duration, and integrity incident.</p>
          </div>
        </div>
        {analytics.students.length ? (
          <div className="student-analytics-list">
            {analytics.students.map((row) => (
              <details key={row.student.id} className={row.integrity_flagged ? "student-analytics flagged" : "student-analytics"}>
                <summary>
                  <span><b>{row.student.name}</b><small>{row.student.email} · {row.classes.map((item) => item.name).join(", ") || "Individually assigned"}</small></span>
                  <span className={`badge ${row.status}`}>{row.status.replace("_", " ")}</span>
                  <span><b>{row.percentage !== undefined && row.percentage !== null ? `${row.percentage}%` : "—"}</b><small>Latest score</small></span>
                  <span><b>{fmtDuration(row.duration_seconds)}</b><small>Time taken</small></span>
                  <span><b>{row.attempt_count}</b><small>Attempts</small></span>
                  <span className={row.integrity_flagged ? "integrity-label" : "integrity-clear"}>{row.integrity_flagged ? "⚑ Flagged" : "Clear"}</span>
                </summary>
                <div className="attempt-breakdown table-wrap">
                  {row.attempts.length ? <table>
                    <thead><tr><th>Attempt</th><th>Outcome</th><th>Score</th><th>Correct / wrong / blank</th><th>Duration</th><th>Integrity</th></tr></thead>
                    <tbody>{row.attempts.map((attempt, index) => (
                      <tr key={attempt.id}>
                        <td>#{row.attempts.length - index}<small>{fmtDate(attempt.started_at)}</small></td>
                        <td><span className={`badge ${attempt.status}`}>{attempt.status}</span></td>
                        <td>{attempt.score ?? "—"} / {attempt.max_score}<small>{attempt.percentage !== undefined && attempt.percentage !== null ? `${attempt.percentage}%` : "—"}</small></td>
                        <td>{attempt.correct} / {attempt.incorrect} / {attempt.unanswered}</td>
                        <td>{attempt.duration_seconds !== undefined && attempt.duration_seconds !== null ? fmtDuration(attempt.duration_seconds) : "In progress"}</td>
                        <td>{attempt.integrity_flagged ? <span className="integrity-label">{(attempt.cancellation_reason || "violation").replaceAll("_", " ")}<small>{fmtDate(attempt.incidents[0]?.detected_at)}</small></span> : <span className="integrity-clear">No incidents</span>}</td>
                      </tr>
                    ))}</tbody>
                  </table> : <p>This student has not started the exam.</p>}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <p>No students are assigned yet.</p>
        )}
      </Card>
    </>
  );
}

function ImportScreen({
  onDone,
  job,
  jobs,
  selectJob,
  openReview,
  createTestFromImport,
  onDeleted,
  refresh,
  reviewOnly = false,
}: {
  onDone: (x: ImportJob) => void;
  job: ImportJob | null;
  jobs: ImportJob[];
  selectJob: (id: string | null) => void;
  openReview?: (id: string) => void;
  createTestFromImport?: (id: string) => void;
  onDeleted: (id: string) => void;
  refresh: () => void;
  reviewOnly?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [deletingId, setDeletingId] = useState<string | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (!job && jobs[0]) selectJob(jobs[0].id);
  }, [job, jobs, selectJob]);
  useEffect(() => {
    if (reviewOnly && job && !job.questions) refresh();
  }, [reviewOnly, job?.id]);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    setBusy(true);
    setError("");
    try {
      const r = await fetch("/api/teacher/imports", {
        method: "POST",
        credentials: "include",
        body: new FormData(form),
      });
      if (!r.ok)
        throw new Error(
          (await r.json().catch(() => ({ detail: "Upload failed" }))).detail ||
            "Upload failed",
        );
      onDone(await r.json());
      form.reset();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const active = job?.status === "uploaded" || job?.status === "processing";
  const deleteImport = async (item: ImportJob) => {
    if (!await confirmApp(`Delete “${item.meta?.paper_title || item.filename || "this import"}”?`)) return;
    setDeletingId(item.id);
    setError("");
    try {
      await api(`/teacher/imports/${item.id}`, { method: "DELETE" });
      onDeleted(item.id);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setDeletingId(null);
    }
  };
  return (
    <div className={`page ${reviewOnly ? "review-workspace" : "narrow"}`}>
      <p className="eyebrow">
        {reviewOnly ? "PAPER REVIEW" : "AI-ASSISTED IMPORT"}
      </p>
      <h1>
        {reviewOnly ? "Review imported papers" : "Import a paper for review"}
      </h1>
      {!reviewOnly && (
        <div className="import-page-content">
          <p className="import-intro">
            Add the question paper and its answer key. You will review every
            extracted question before using it in an assessment.
          </p>
          <Card className="paper-import-card">
            <form onSubmit={submit} className="stacked-form">
              <label className="paper-name-field">
                Paper name <span className="optional">(optional)</span>
                <input name="paper_title" placeholder="e.g. JEE Main Physics — Set A" />
              </label>
              <div className="paper-upload-fields">
                <label className="paper-upload-field">
                  <span>Question paper</span>
                  <small>PDF · questions and diagrams</small>
                  <input required name="question_paper" type="file" accept="application/pdf" />
                </label>
                <label className="paper-upload-field">
                  <span>Answer key & solutions</span>
                  <small>PDF · required for review</small>
                  <input required name="answer_key" type="file" accept="application/pdf" />
                </label>
              </div>
              <div className="import-submit-row">
                <small>Extraction begins immediately. Review happens in the dedicated paper workspace.</small>
                <button disabled={busy}>{busy ? "Uploading paper…" : "Upload and start extraction"}</button>
              </div>
            </form>
            {error && <Notice kind="error">{error}</Notice>}
          </Card>
          <section className="recent-imports" aria-label="Recent imports">
            <div className="recent-imports-heading">
              <h2>Recent imports</h2>
              {jobs.length > 3 && <span>{jobs.length} total</span>}
            </div>
            {jobs.length ? jobs.slice(0, 3).map((item) => (
            <div className="recent-import-row" key={item.id}>
              <button type="button" onClick={() => openReview ? openReview(item.id) : selectJob(item.id)}>
                <span>
                  <b>{item.meta?.paper_title || item.filename || "Question paper"}</b>
                  <small>{item.meta?.question_count || 0} questions · {item.meta?.phase || "Queued"}</small>
                </span>
                <span className={`badge ${item.status}`}>{item.status}</span>
              </button>
              <button
                type="button"
                className="text-danger"
                disabled={deletingId === item.id || item.status === "uploaded" || item.status === "processing"}
                onClick={() => deleteImport(item)}
              >
                {deletingId === item.id ? "Deleting…" : "Delete"}
              </button>
            </div>
            )) : <p>No papers imported yet.</p>}
          </section>
        </div>
      )}
      {reviewOnly && (
        <div className="review-layout">
          <aside className="paper-queue">
            <div className="paper-queue-heading"><h2>Paper queue</h2><span>{jobs.length}</span></div>
            {jobs.length ? (
              jobs.map((item) => (
                <button
                  className={item.id === job?.id ? "selected" : ""}
                  key={item.id}
                  onClick={() => selectJob(item.id)}
                >
                  <b>
                    {item.meta?.paper_title ||
                      item.filename ||
                      "Question paper"}
                  </b>
                  <span className={`badge ${item.status}`}>{item.status}</span>
                  <small>
                    {item.meta?.question_count || 0} questions ·{" "}
                    {item.meta?.phase || "Queued"}
                    {jobs.filter((other) => (other.meta?.paper_title || other.filename) === (item.meta?.paper_title || item.filename)).length > 1 && ` · ID ${item.id.slice(0, 8)}`}
                  </small>
                </button>
              ))
            ) : (
              <p>No papers uploaded yet.</p>
            )}
          </aside>
          <div className="review-main">
            {job && (
              <ImportReview job={job} active={!!active} refresh={refresh} createTest={() => createTestFromImport?.(job.id)} />
            )}
          </div>
          {job && (
            <aside className="review-health">
              <p className="eyebrow">IMPORT HEALTH</p>
              <h2>Review progress</h2>
              <strong>{job.questions?.filter((q) => q.approved).length || 0}<span> / {job.questions?.length || job.meta?.question_count || 0}</span></strong>
              <small>questions approved</small>
              <hr />
              <div><b>Status</b><span className={`badge ${job.status}`}>{job.status}</span></div>
              <div><b>Extraction</b><small>{job.meta?.phase || "Checking import status"}</small></div>
              {job.meta?.failed_pages?.length ? <Notice kind="error">{job.meta.failed_pages.length} page(s) need attention.</Notice> : null}
              <a href={`/api/teacher/imports/${job.id}/source`} target="_blank" rel="noreferrer">Open source PDF</a>
              {job.status === "approved" && createTestFromImport && (
                <button type="button" onClick={() => createTestFromImport(job.id)}>Create test from this paper</button>
              )}
              <button
                type="button"
                className="text-danger"
                disabled={active || deletingId === job.id}
                onClick={() => deleteImport(job)}
              >
                {deletingId === job.id ? "Deleting…" : "Delete import"}
              </button>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}
function ImportReview({
  job,
  active,
  refresh,
  createTest,
}: {
  job: ImportJob;
  active: boolean;
  refresh: () => void;
  createTest: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [questionIndex, setQuestionIndex] = useState(0);
  useEffect(() => setQuestionIndex(0), [job.id]);
  const reviewQuestions = job.questions || [],
    currentQuestion = reviewQuestions[questionIndex];
  useEffect(() => {
    setQuestionIndex((index) => Math.min(index, Math.max(0, reviewQuestions.length - 1)));
  }, [reviewQuestions.length]);
  const moveToQuestion = (nextIndex: number) => {
    setQuestionIndex(nextIndex);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const questionDeleted = () => {
    setQuestionIndex((index) => Math.max(0, Math.min(index, reviewQuestions.length - 2)));
    void refresh();
  };
  const reextract = async () => {
    setBusy(true);
    try {
      await api(`/teacher/imports/${job.id}/reextract`, { method: "POST" });
      await refresh();
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Card className="import-status">
        <div className="status-heading">
          <div>
            <p className="eyebrow">IMPORT PROGRESS</p>
            <h2>{job.meta?.paper_title || job.filename || "Question paper"}</h2>
            <p>
              {job.meta?.phase || "Checking import status"}
              {job.meta?.question_count !== undefined
                ? ` — ${job.meta.question_count} questions extracted.`
                : ""}
            </p>
          </div>
          <span className={`badge ${job.status}`}>{job.status}</span>
        </div>
        {active && (
          <div className="progress">
            <i />
            <i />
            <i />
          </div>
        )}
        <div className="review-actions">
          <a
            href={`/api/teacher/imports/${job.id}/source`}
            target="_blank"
            rel="noreferrer"
          >
            Open source PDF
          </a>
          {!active && job.status !== "approved" && (
            <button
              className="secondary-action"
              onClick={reextract}
              disabled={busy}
            >
              {busy ? "Restarting…" : "Re-extract paper"}
            </button>
          )}
          {job.status === "approved" && (
            <button onClick={createTest}>Create test from this paper</button>
          )}
          <button onClick={refresh}>Refresh</button>
        </div>
        {job.meta?.failed_pages?.length ? (
          <Notice kind="error">
            {job.meta.failed_pages.length} page(s) could not be read after
            retries: {job.meta.failed_pages.map((item) => item.page).join(", ")}
            . Re-extract this paper to retry them.
          </Notice>
        ) : null}
        {job.error && <Notice kind="error">{job.error}</Notice>}
      </Card>
      {active ? (
        <Card>
          <p>
            The extraction worker is processing this paper. It will update
            automatically while this view is open.
          </p>
        </Card>
      ) : currentQuestion ? (
        <>
          <div className="review-navigation" aria-label="Question navigation">
            <div className="review-question-context">
              <span>Question {questionIndex + 1} of {reviewQuestions.length}</span>
              <span>{currentQuestion.kind.replaceAll("_", " ")}</span>
            </div>
            <div className="review-jump-control">
              <label htmlFor="review-question-select">Jump to question</label>
              <select id="review-question-select" value={questionIndex} onChange={(event) => moveToQuestion(Number(event.target.value))}>
                {reviewQuestions.map((question, index) => (
                  <option key={question.id} value={index}>Question {index + 1} — {question.approved ? "Approved" : "Needs review"}</option>
                ))}
              </select>
            </div>
            <div className="review-pagination">
              <button type="button" className="secondary-action" disabled={!questionIndex} onClick={() => moveToQuestion(questionIndex - 1)}>Previous</button>
              <span>{questionIndex + 1} / {reviewQuestions.length}</span>
              <button type="button" className="secondary-action" disabled={questionIndex === reviewQuestions.length - 1} onClick={() => moveToQuestion(questionIndex + 1)}>Next question</button>
            </div>
          </div>
          <ReviewQuestion
            key={currentQuestion.id}
            jobId={job.id}
            question={currentQuestion}
            onChanged={refresh}
            onDeleted={questionDeleted}
          />
        </>
      ) : (
        <Card><p>No extracted questions are ready to review yet.</p></Card>
      )}
    </>
  );
}
function answerOptionIndex(question: Question) {
  const answer = question.answer;
  if (typeof answer?.option === "number") return answer.option;
  const value = answer?.value ?? answer?.correct_option;
  if (value === undefined) return -1;
  return question.options.findIndex(
    (option: any, index) =>
      String(value).toLowerCase() === String(index).toLowerCase() ||
      String(value).toLowerCase() === String(index + 1).toLowerCase() ||
      String(value).toLowerCase() ===
        String.fromCharCode(65 + index).toLowerCase() ||
      String(value).toLowerCase() === String(option?.value || "").toLowerCase(),
  );
}
function ReviewQuestion({
  jobId,
  question,
  onChanged,
  onDeleted,
}: {
  jobId: string;
  question: Question;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const multiple = question.kind === "multiple_choice",
    written = ["numerical", "integer", "subjective"].includes(question.kind),
    [selected, setSelected] = useState<number[]>(() =>
      multiple
        ? Array.isArray(question.answer?.options)
          ? question.answer.options
          : []
        : [answerOptionIndex(question)],
    ),
    [value, setValue] = useState(() => String(question.answer?.value ?? "")),
    [confidence, setConfidence] = useState(() =>
      Math.round(
        Math.min(1, Math.max(0, Number(question.confidence ?? 0))) * 100,
      ),
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const save = async (approve = false) => {
    setBusy(true);
    setError("");
    try {
      const answer = written
        ? { value }
        : multiple
          ? { options: selected }
          : { option: selected[0] };
      if ((written && !value.trim()) || (!written && !selected.length))
        throw new Error("Enter a reviewed answer key before approval");
      await api(
        `/teacher/imports/${jobId}/questions/${question.id}/answer-key`,
        {
          method: "PUT",
          body: JSON.stringify({ answer, confidence: confidence / 100 }),
        },
      );
      if (approve)
        await api(
          `/teacher/imports/${jobId}/questions/${question.id}/approve`,
          { method: "POST" },
        );
      await onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const toggle = (index: number) =>
    setSelected((current: number[]) =>
      multiple
        ? current.includes(index)
          ? current.filter((x: number) => x !== index)
          : [...current, index].sort((a: number, b: number) => a - b)
        : [index],
    );
  const ignoreWarningAndApprove = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/teacher/imports/${jobId}/questions/${question.id}/ignore-warnings`, { method: "POST" });
      await onChanged();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const deleteQuestion = async () => {
    if (!await confirmApp("Delete this extracted question? This cannot be undone.")) return;
    setBusy(true);
    setError("");
    try {
      await api(`/teacher/imports/${jobId}/questions/${question.id}`, { method: "DELETE" });
      onDeleted();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const hasWarnings = (question.interaction?.ingestion_issues || []).length > 0;
  return (
    <Card className="review-question">
      <QuestionCard q={question} mode="teacher" />
      {needsFigureAdjustment(question) && <FigureEditor questionId={question.id} options={question.options} api={api} done={onChanged} />}
      {hasWarnings && <Notice kind="error">
        Needs review: {question.interaction.ingestion_issues.join(", ")}
      </Notice>}
      {!question.approved && (
        <div className="answer-key-editor">
          <div>
            <strong>Teacher answer key</strong>
            {written ? (
              <input
                aria-label="Answer value"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={
                  question.kind === "integer"
                    ? "Enter integer answer"
                    : "Enter numerical answer"
                }
              />
            ) : (
              <div className="answer-options">
                {question.options.map((_: any, index: number) => (
                  <button
                    type="button"
                    key={index}
                    className={selected.includes(index) ? "selected" : ""}
                    aria-pressed={selected.includes(index)}
                    aria-label={`Answer option ${String.fromCharCode(65 + index)}`}
                    onClick={() => toggle(index)}
                  >
                    {String.fromCharCode(65 + index)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <label>
            Confidence{" "}
            <input
              type="number"
              min="0"
              max="100"
              value={confidence}
              onChange={(e) =>
                setConfidence(
                  Math.min(100, Math.max(0, Number(e.target.value))),
                )
              }
            />
            <span>%</span>
          </label>
          <div className="review-buttons">
            <button
              type="button"
              className="secondary-action"
              onClick={() => save(false)}
              disabled={busy}
            >
              Save answer key
            </button>
            <button type="button" onClick={() => save(true)} disabled={busy}>
              {busy ? "Saving…" : "Approve question"}
            </button>
            {hasWarnings && <button type="button" className="secondary-action" onClick={ignoreWarningAndApprove} disabled={busy}>
              Ignore all warnings & approve
            </button>}
          </div>
          {error && <Notice kind="error">{error}</Notice>}
        </div>
      )}
      <div className="review-question-actions">
        <button type="button" className="text-danger" onClick={deleteQuestion} disabled={busy}>
          Delete question
        </button>
      </div>
    </Card>
  );
}

function Builder({
  initialQuestions,
  initialQuestionCount,
  imports,
  initialImportId,
  onDone,
}: {
  initialQuestions: Question[];
  initialQuestionCount: number;
  imports: ImportJob[];
  initialImportId: string | null;
  onDone: (id: string, publishedFromImport: boolean) => void;
}) {
  const [sections, setSections] = useState<{ id: string; title: string; questionIds: string[] }[]>([{ id: "section-1", title: "Section 1", questionIds: [] }]),
    [activeSectionId, setActiveSectionId] = useState("section-1"),
    [selectedCache, setSelectedCache] = useState<Record<string, Question>>({}),
    [error, setError] = useState(""),
    [banks, setBanks] = useState<any[]>([]),
    [bankId, setBankId] = useState(""),
    [nodes, setNodes] = useState<any[]>([]),
    [source, setSource] = useState<"bank" | "mine">("mine"),
    [paperId, setPaperId] = useState(initialImportId || ""),
    [includeWholePaper, setIncludeWholePaper] = useState(!!initialImportId),
    [sourceJobs, setSourceJobs] = useState<ImportJob[]>(imports),
    [subjectId, setSubjectId] = useState(""),
    [chapterId, setChapterId] = useState(""),
    [subtopicId, setSubtopicId] = useState(""),
    [kinds, setKinds] = useState<string[]>([]),
    [page, setPage] = useState(1),
    [remote, setRemote] = useState<{
      items: Question[];
      total: number;
      has_more: boolean;
    }>({ items: [], total: 0, has_more: false }),
    [mine, setMine] = useState<QuestionPage>({ items: initialQuestions, page: 1, page_size: 20, total: initialQuestionCount, has_more: initialQuestionCount > initialQuestions.length }),
    [availability, setAvailability] = useState<{
      total: number;
      counts: Record<string, number>;
    }>({ total: 0, counts: {} }),
    [loading, setLoading] = useState(false),
    [countsLoading, setCountsLoading] = useState(false);
  const selected = sections.flatMap((section) => section.questionIds);
  const activeSection = sections.find((section) => section.id === activeSectionId) || sections[0];
  const activeBank = banks.find((bank) => bank.id === bankId);
  const sourceImports = sourceJobs.filter((item) => (item.approved_count || 0) > 0),
    activePaper = sourceImports.find((item) => item.id === paperId),
    directPaperMode = source === "mine" && activePaper?.status === "approved" && includeWholePaper;
  useEffect(() => {
    api("/teacher/import-sources").then(setSourceJobs).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (!initialImportId) return;
    setSource("mine");
    setPaperId(initialImportId);
    setIncludeWholePaper(true);
    setPage(1);
  }, [initialImportId]);
  useEffect(() => {
    api("/question-banks")
      .then((rows: any[]) => {
        setBanks(rows);
        const defaultBank =
          rows.find((bank) => bank.slug === "jee-advanced") || rows[0];
        if (defaultBank) setBankId(defaultBank.id);
        else setSource("mine");
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    setNodes([]);
    setSubjectId("");
    setChapterId("");
    setSubtopicId("");
    setKinds([]);
    setPage(1);
    setRemote({ items: [], total: 0, has_more: false });
    setAvailability({ total: 0, counts: {} });
  }, [bankId]);
  useEffect(() => {
    if (!bankId) return;
    api(`/question-banks/${bankId}/taxonomy`)
      .then(setNodes)
      .catch((e) => setError(e.message));
  }, [bankId]);
  useEffect(() => {
    if (source !== "bank" || !bankId) return;
    const params = new URLSearchParams();
    if (subjectId) params.set("subject_id", subjectId);
    if (chapterId) params.set("chapter_id", chapterId);
    if (subtopicId) params.set("subtopic_id", subtopicId);
    setCountsLoading(true);
    api(`/question-banks/${bankId}/question-counts?${params}`)
      .then(setAvailability)
      .catch((e) => setError(e.message))
      .finally(() => setCountsLoading(false));
  }, [source, bankId, subjectId, chapterId, subtopicId]);
  useEffect(() => {
    if (source !== "bank" || !bankId) return;
    const params = new URLSearchParams({ page: String(page), page_size: "20" });
    if (subjectId) params.set("subject_id", subjectId);
    if (chapterId) params.set("chapter_id", chapterId);
    if (subtopicId) params.set("subtopic_id", subtopicId);
    kinds.forEach((kind) => params.append("kinds", kind));
    setLoading(true);
    api(`/question-banks/${bankId}/questions?${params}`)
      .then(setRemote)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [source, bankId, subjectId, chapterId, subtopicId, kinds, page]);
  useEffect(() => {
    if (source !== "mine") return;
    const params = new URLSearchParams({ page: String(page), page_size: "20", approved: "true" });
    if (paperId) params.set("import_job_id", paperId);
    setLoading(true);
    api(`/questions?${params}`)
      .then(setMine)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [source, paperId, page]);
  const subjects = nodes.filter((node) => node.kind === "subject"),
    chapters = nodes.filter(
      (node) => node.kind === "chapter" && node.parent_id === subjectId,
    ),
    subtopics = nodes.filter(
      (node) => node.kind === "topic" && node.parent_id === chapterId,
    ),
    shown =
      source === "bank"
        ? remote.items
        : mine.items,
    selectedQuestions = selected.map((id) => shown.find((question) => question.id === id) || selectedCache[id]).filter(Boolean) as Question[],
    toggleKind = (kind: string) => {
      setPage(1);
      setKinds((current) =>
        current.includes(kind)
          ? current.filter((value) => value !== kind)
          : [...current, kind],
      );
    },
    submit = async (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      try {
        const f = Object.fromEntries(new FormData(e.currentTarget));
        const toIso = (value: FormDataEntryValue | undefined) =>
          value ? new Date(String(value)).toISOString() : null;
        if (!directPaperMode && sections.some((section) => !section.title.trim() || !section.questionIds.length)) {
          throw new Error("Give every section a name and add at least one question, or remove the empty section.");
        }
        const questionIds = directPaperMode ? [] : selected;
        const payload: any = {
          ...f,
          opens_at: toIso(f.opens_at),
          closes_at: toIso(f.closes_at),
          duration_minutes: Number(f.duration_minutes),
          max_attempts: Number(f.max_attempts),
          marks_per_question: Number(f.marks_per_question),
          results_policy: { mode: "immediate" },
        };
        if (!directPaperMode) payload.question_ids = questionIds;
        payload.delivery_blueprint = {
          preset: String(f.blueprint_preset || "jee-practice"),
          instructions: String(f.candidate_instructions || "Read every question carefully. Use Save & Next to record an answer.").trim(),
          languages: ["en"],
          accommodations: {},
          sections: directPaperMode ? [{ id: "section-1", title: "Section 1", question_ids: [], navigation: "free", optional_groups: [] }] : sections.map((section) => ({
            id: section.id,
            title: section.title.trim(),
            question_ids: section.questionIds,
            navigation: "free",
            optional_groups: [],
          })),
        };
        const result = await api(directPaperMode ? `/teacher/imports/${paperId}/tests` : "/tests", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        onDone(result.id, directPaperMode);
      } catch (e: any) {
        setError(e.message);
      }
    };
  return (
    <div className="page">
      <p className="eyebrow">TEST BUILDER</p>
      <h1>Create a governed assessment</h1>
      {error && <Notice kind="error">{error}</Notice>}
      <form onSubmit={submit} className="builder builder-workspace">
        <Card className="builder-settings">
          <h2>Assessment details</h2>
          <p className="builder-settings-help">Set the essentials, then add questions.</p>
          <div className="stacked-form">
            <div>
              <label htmlFor="builder-title">Test title</label>
              <input id="builder-title" name="title" required />
            </div>
            <div>
              <label htmlFor="builder-subject">Subject</label>
              <input id="builder-subject" name="subject" required />
            </div>
            <div>
              <label htmlFor="builder-duration_minutes">Duration (minutes)</label>
              <input
                id="builder-duration_minutes" name="duration_minutes"
                type="number"
                min="1"
                defaultValue="60"
                required
              />
            </div>
            <div>
              <label htmlFor="builder-max_attempts">Maximum attempts</label>
              <input
                id="builder-max_attempts" name="max_attempts"
                type="number"
                min="1"
                defaultValue="1"
                required
              />
            </div>
            <div>
              <label htmlFor="builder-marks_per_question">Marks per question</label>
              <input id="builder-marks_per_question" name="marks_per_question" type="number" min="1" defaultValue="4" required />
            </div>
            <div>
              <label htmlFor="builder-blueprint_preset">CBT preset</label>
              <select id="builder-blueprint_preset" name="blueprint_preset" defaultValue="jee-practice">
                <option value="jee-practice">JEE-style practice CBT</option>
                <option value="custom">Custom practice CBT</option>
              </select>
            </div>
            <div className="builder-wide-field">
              <label htmlFor="builder-candidate_instructions">Candidate instructions</label>
              <textarea id="builder-candidate_instructions" name="candidate_instructions" rows={3} defaultValue="Read every question carefully. Use Save & Next to record an answer. Mark for Review & Next saves your response and flags it for review." />
              <small>{directPaperMode
                ? "Every approved question type from this source PDF is included in the published test."
                : "New CBT papers use an explicit delivery blueprint. Subjective questions require manual evaluation and cannot be published in this mode."}</small>
            </div>
            <div>
              <label htmlFor="builder-opens_at">
                Opens at <span className="optional">(optional)</span>
              </label>
              <input id="builder-opens_at" name="opens_at" type="datetime-local" />
            </div>
            <div>
              <label htmlFor="builder-closes_at">
                Closes at <span className="optional">(optional)</span>
              </label>
              <input id="builder-closes_at" name="closes_at" type="datetime-local" />
            </div>
          </div>
        </Card>
        <Card className="builder-selection">
          <div className="builder-selection-heading">
            <span>Assessment sections</span>
            <b>{directPaperMode ? activePaper.approved_count || mine.total : selected.length}</b>
          </div>
          {directPaperMode ? <div className="builder-selection-list"><div className="paper-selection-summary"><b>Entire PDF · one section</b><small>Every approved question will be included and the test published immediately. Turn off “Use entire PDF” to create custom sections.</small></div></div> : <div className="builder-sections">
            <p>Choose a section, then select questions from the library. Each question belongs to one section.</p>
            {sections.map((section, index) => <section className={`builder-section ${section.id === activeSectionId ? "active" : ""}`} key={section.id}>
              <div className="builder-section-heading">
                <button type="button" className="secondary-action" aria-pressed={section.id === activeSectionId} onClick={() => setActiveSectionId(section.id)}>{section.id === activeSectionId ? "Adding here" : "Add here"}</button>
                <span>{section.questionIds.length} {section.questionIds.length === 1 ? "question" : "questions"}</span>
              </div>
              <label htmlFor={`section-title-${section.id}`}>Section {index + 1} name</label>
              <input id={`section-title-${section.id}`} value={section.title} maxLength={100} onChange={(event) => setSections((current) => current.map((item) => item.id === section.id ? { ...item, title: event.target.value } : item))} />
              {section.questionIds.length > 0 && <details className="section-questions"><summary>Manage questions</summary>
                {section.questionIds.map((id) => {
                  const question = selectedQuestions.find((item) => item.id === id);
                  const label = question ? questionNumber(question.source_number) : "Question";
                  return <div className="section-question" key={id}>
                    <span>{label}</span>
                    {sections.length > 1 && <label>Move <span className="sr-only">{label} to section</span><select aria-label={`Move ${label} to section`} value={section.id} onChange={(event) => { const target = event.target.value; setSections((current) => current.map((item) => item.id === section.id ? { ...item, questionIds: item.questionIds.filter((qid) => qid !== id) } : item.id === target ? { ...item, questionIds: [...item.questionIds, id] } : item)); }}>{sections.map((item, i) => <option key={item.id} value={item.id}>{item.title.trim() || `Section ${i + 1}`}</option>)}</select></label>}
                    <button type="button" className="text-danger" aria-label={`Remove ${label} from assessment`} onClick={() => setSections((current) => current.map((item) => item.id === section.id ? { ...item, questionIds: item.questionIds.filter((qid) => qid !== id) } : item))}>Remove</button>
                  </div>;
                })}
              </details>}
              {sections.length > 1 && <button type="button" className="text-danger section-remove" disabled={section.questionIds.length > 0} title={section.questionIds.length ? "Move or remove this section’s questions first" : undefined} onClick={() => { setSections((current) => current.filter((item) => item.id !== section.id)); if (activeSectionId === section.id) setActiveSectionId(sections.find((item) => item.id !== section.id)!.id); }}>Remove empty section</button>}
            </section>)}
            <button type="button" className="secondary-action add-section" onClick={() => { const id = `section-${crypto.randomUUID()}`; setSections((current) => [...current, { id, title: `Section ${current.length + 1}`, questionIds: [] }]); setActiveSectionId(id); }}>Add section</button>
          </div>}
          <p className="builder-submit-help">{directPaperMode ? "Test title and subject are required before publishing." : "Name each section, add at least one question to each, and enter a test title and subject."}</p>
          <button className="builder-selection-submit" disabled={directPaperMode ? !(activePaper.approved_count || mine.total) : !selected.length}>
            {directPaperMode
              ? `Create & publish (${activePaper.approved_count || mine.total})`
              : `Create draft${selected.length ? ` (${selected.length})` : ""}`}
          </button>
        </Card>
        <Card className="builder-library">
          <h2>
            {source === "bank"
              ? `${activeBank?.name || "Global"} question bank`
              : activePaper
                ? `Approved questions from ${activePaper.meta?.paper_title || activePaper.filename || "selected paper"}`
                : "Your approved questions"}
          </h2>
          <div className="stacked-form compact-form builder-source-controls">
            <label>
              Question source
              <select
                value={source}
                onChange={(e) => {
                  setSource(e.target.value as "bank" | "mine");
                  setPage(1);
                }}
              >
                <option value="bank" disabled={!bankId}>Global bank</option>
                <option value="mine">My approved questions</option>
              </select>
            </label>
            {source === "bank" && (
              <>
                <label>
                  Global bank
                  <select
                    value={bankId}
                    onChange={(e) => setBankId(e.target.value)}
                  >
                    {banks.map((bank) => (
                      <option key={bank.id} value={bank.id}>
                        {bank.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Subject
                  <select
                    value={subjectId}
                    onChange={(e) => {
                      setSubjectId(e.target.value);
                      setChapterId("");
                      setSubtopicId("");
                      setPage(1);
                    }}
                  >
                    <option value="">All subjects</option>
                    {subjects.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Chapter
                  <select
                    value={chapterId}
                    disabled={!subjectId}
                    onChange={(e) => {
                      setChapterId(e.target.value);
                      setSubtopicId("");
                      setPage(1);
                    }}
                  >
                    <option value="">All chapters</option>
                    {chapters.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Subtopic
                  <select
                    value={subtopicId}
                    disabled={!chapterId}
                    onChange={(e) => {
                      setSubtopicId(e.target.value);
                      setPage(1);
                    }}
                  >
                    <option value="">All subtopics</option>
                    {subtopics.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name}
                      </option>
                    ))}
                  </select>
                </label>
                <details className="question-type-list">
                  <summary>
                    Question type <span className="optional">({countsLoading ? "updating…" : `${availability.total} available`})</span>
                    <b>{kinds.length ? `${kinds.length} selected` : "All types"}</b>
                  </summary>
                  <div>
                  {JEE_QUESTION_TYPES.map(([value, label]) => (
                    <label key={value} className="assignment-choice">
                      <input
                        type="checkbox"
                        checked={kinds.includes(value)}
                        onChange={() => toggleKind(value)}
                      />
                      <span>{label}</span>
                      <b className="question-type-count">
                        {countsLoading ? "…" : availability.counts[value] || 0}
                      </b>
                    </label>
                  ))}
                  </div>
                </details>
              </>
            )}
            {source === "mine" && (
              <label>
                Source PDF
                <select
                  value={paperId}
                  onChange={(e) => {
                    setPaperId(e.target.value);
                    setIncludeWholePaper(false);
                    setPage(1);
                    }}
                >
                  <option value="">All approved PDFs</option>
                  {sourceImports.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.meta?.paper_title || item.filename || "Imported paper"} ({item.approved_count}){sourceImports.filter((other) => (other.meta?.paper_title || other.filename) === (item.meta?.paper_title || item.filename)).length > 1 ? ` · ${item.id.slice(0, 8)}` : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {source === "mine" && activePaper?.status === "approved" && (
              <label className="assignment-choice builder-paper-mode">
                <input type="checkbox" checked={includeWholePaper} onChange={(e) => setIncludeWholePaper(e.target.checked)} />
                <span><b>Use entire PDF</b><small>Create and publish every approved question in one section. Turn this off to assign questions to custom sections.</small></span>
              </label>
            )}
          </div>
          {!directPaperMode && <div className="builder-active-section">Adding questions to <strong>{activeSection.title.trim() || "Untitled section"}</strong>. Choose another section in the assessment panel.</div>}
          <div className="builder-page-top" aria-label="Question library pages">
            <span>{(source === "bank" ? remote.total : mine.total) ? `${(page - 1) * 20 + 1}–${(page - 1) * 20 + shown.length} of ${source === "bank" ? remote.total : mine.total} questions · Page ${page}` : "No matching questions"}</span>
            <button type="button" className="secondary-action" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</button>
            <button type="button" className="secondary-action" disabled={source === "bank" ? !remote.has_more : !mine.has_more} onClick={() => setPage((value) => value + 1)}>Next</button>
          </div>
          <div className="builder-question-list">
            {loading ? (
              <p>Loading filtered questions…</p>
            ) : shown.length ? (
              shown.map((q) => (
                <div className="selectq" key={q.id}>
                  <input
                    aria-label={`Include ${questionNumber(q.source_number)}`}
                    type="checkbox"
                    checked={directPaperMode || selected.includes(q.id)}
                    disabled={directPaperMode}
                    onChange={() => {
                      setSelectedCache((cache) => ({ ...cache, [q.id]: q }));
                      setSections((current) => selected.includes(q.id)
                        ? current.map((section) => ({ ...section, questionIds: section.questionIds.filter((id) => id !== q.id) }))
                        : current.map((section) => section.id === activeSectionId ? { ...section, questionIds: [...section.questionIds, q.id] } : section));
                    }}
                  />
                  {!directPaperMode && selected.includes(q.id) && <p className="builder-assigned-label">Added to {sections.find((section) => section.questionIds.includes(q.id))?.title || "Untitled section"}</p>}
                  <QuestionCard q={q} mode="teacher" />
                </div>
              ))
            ) : (
              <p>
                {source === "bank"
                  ? "No published questions match these filters yet."
                  : paperId
                    ? "This paper has no approved questions."
                    : "Approve questions from an imported paper before building a test."}
              </p>
            )}
          </div>
          {(source === "bank" || source === "mine") && (
            <div className="review-actions">
              <small role="status">{(source === "bank" ? remote.total : mine.total) ? `${(page - 1) * 20 + 1}–${(page - 1) * 20 + shown.length} of ${source === "bank" ? remote.total : mine.total} questions · Page ${page}` : "No matching questions"}</small>
              <button
                type="button"
                className="secondary-action"
                disabled={page === 1}
                onClick={() => setPage((value) => value - 1)}
              >
                Previous
              </button>
              <button
                type="button"
                className="secondary-action"
                disabled={source === "bank" ? !remote.has_more : !mine.has_more}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </button>
            </div>
          )}
        </Card>
      </form>
    </div>
  );
}
function StudentHome({
  user,
  tests,
  classes,
  start,
  viewResult,
}: {
  user: User;
  tests: Test[];
  classes: StudentClass[];
  start: (id: string) => void;
  viewResult: (id: string) => void;
}) {
  const completed = tests.filter(
    (test) => test.latest_attempt?.status === "submitted",
  ).length;
  const inProgress = tests.filter(
    (test) => test.latest_attempt?.status === "in_progress",
  ).length;
  const pending = Math.max(0, tests.length - completed);
  const initials = user.name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="page student-dashboard">
      <div className="student-welcome">
        <div>
          <p className="eyebrow">STUDENT PORTAL</p>
          <h1>Welcome back, {user.name.split(" ")[0]}</h1>
          <p>Keep track of your assigned assessments and upcoming deadlines.</p>
        </div>
        <div className="student-profile">
          <div className="avatar" aria-hidden="true">
            {initials}
          </div>
          <div>
            <b>{user.name}</b>
            <small>{user.email}</small>
          </div>
        </div>
      </div>
      <section className="student-overview">
        <Card className="enrolment-card">
          <div className="card-label">STUDENT DETAILS</div>
          <h2>{user.name}</h2>
          <dl>
            <div>
              <dt>Email</dt>
              <dd>{user.email}</dd>
            </div>
            <div>
              <dt>Enrolled class{classes.length === 1 ? "" : "es"}</dt>
              <dd>
                {classes.length
                  ? classes.map((classroom) => classroom.name).join(", ")
                  : "No class enrolment yet"}
              </dd>
            </div>
          </dl>
          {classes.some((classroom) => classroom.description) && (
            <p className="class-note">
              {classes
                .map((classroom) => classroom.description)
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
        </Card>
        <div className="student-stats">
          <Card>
            <span className="stat-icon assigned">◫</span>
            <b>{tests.length}</b>
            <span>Tests assigned</span>
          </Card>
          <Card>
            <span className="stat-icon completed">✓</span>
            <b>{completed}</b>
            <span>Tests appeared</span>
          </Card>
          <Card>
            <span className="stat-icon pending">◷</span>
            <b>{pending}</b>
            <span>Still to complete</span>
          </Card>
          <Card>
            <span className="stat-icon progress">↗</span>
            <b>{inProgress}</b>
            <span>In progress</span>
          </Card>
        </div>
      </section>
      {tests.some((test) => test.availability === "available" && test.latest_attempt?.status !== "submitted") && (
        <Card className="student-next-step">
          <div>
            <p className="eyebrow">NEXT UP</p>
            <h2>{tests.find((test) => test.latest_attempt?.status === "in_progress")?.title || tests.find((test) => test.availability === "available")?.title}</h2>
            <p>{tests.find((test) => test.latest_attempt?.status === "in_progress") ? "You have an assessment in progress. Resume when you are ready." : "This assessment is available now. Check the duration before you begin."}</p>
          </div>
          <button onClick={() => start((tests.find((test) => test.latest_attempt?.status === "in_progress") || tests.find((test) => test.availability === "available"))!.id)}>
            {tests.some((test) => test.latest_attempt?.status === "in_progress") ? "Resume assessment" : "Review & start"}
          </button>
        </Card>
      )}
      <StudentProgress />
      <div className="assessment-heading">
        <div>
          <p className="eyebrow">MY ASSESSMENTS</p>
          <h2>Assigned assessments</h2>
        </div>
        <span>{tests.length} total</span>
      </div>
      <div className="cards student-test-cards">
        {tests.length ? (
          [...tests].sort((a, b) => Number(b.latest_attempt?.status === "in_progress") - Number(a.latest_attempt?.status === "in_progress") || Number(b.availability === "available") - Number(a.availability === "available")).map((t) => {
            const latest = t.latest_attempt;
            const submitted = latest?.status === "submitted";
            const cancelled = latest?.status === "cancelled";
            const inProgress = latest?.status === "in_progress";
            const canStart =
              t.availability === "available" &&
              (inProgress || Boolean(t.attempts_remaining));
            return (
              <Card key={t.id}>
                <div className="test-card-top">
                  <span
                    className={`badge ${cancelled ? "cancelled" : submitted ? "submitted" : inProgress ? "in_progress" : t.availability}`}
                  >
                    {cancelled
                      ? "cancelled · flagged"
                      : submitted
                      ? "completed"
                      : inProgress
                        ? "in progress"
                        : t.availability}
                  </span>
                  {t.closes_at && <small>Due {fmtDate(t.closes_at)}</small>}
                </div>
                <h2>{t.title}</h2>
                <p>
                  {t.subject} · {t.question_count} questions ·{" "}
                  {Math.ceil(t.duration_seconds / 60)} minutes
                </p>
                {t.opens_at && <small>Opens: {fmtDate(t.opens_at)}</small>}
                {submitted && (
                  <small>
                    Submitted: {fmtDate(latest.submitted_at)} · Score:{" "}
                    {latest.score ?? "—"} · Time taken: {fmtDuration(latest.duration_seconds)}
                  </small>
                )}
                {cancelled && (
                  <small className="integrity-label">
                    Integrity incident: {(latest.cancellation_reason || "secure exam violation").replaceAll("_", " ")} · {fmtDate(latest.submitted_at)}
                  </small>
                )}
                <div className="student-actions">
                  {submitted && (
                    <button
                      className="secondary-action"
                      onClick={() => viewResult(latest.id)}
                    >
                      View result & solutions
                    </button>
                  )}
                  {canStart && (
                    <button onClick={() => start(t.id)}>
                      {inProgress
                        ? "Resume assessment"
                        : submitted || cancelled
                          ? "Start another attempt"
                          : "Start assessment"}
                    </button>
                  )}
                  {!canStart && !submitted && !cancelled && (
                    <button disabled>
                      {inProgress
                        ? "Assessment closed"
                        : t.availability === "upcoming"
                          ? "Not open yet"
                          : t.availability === "closed"
                            ? "Assessment closed"
                            : "Attempt limit reached"}
                    </button>
                  )}
                  {cancelled && !canStart && <button disabled>Attempt cancelled</button>}
                </div>
              </Card>
            );
          })
        ) : (
          <Card className="empty-assessments">
            <h2>No assessments yet</h2>
            <p>
              Your teacher will add tests here after assigning them to your
              class.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}
function FolderInput({ name }: { name: string; required?: boolean }) {
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  return (
    <div className="file-pickers" onChange={(event) => {
      const input = event.target as HTMLInputElement;
      const key = input.hasAttribute("webkitdirectory") ? "folder" : "files";
      setSelected((current) => ({ ...current, [key]: Array.from(input.files || []).map((file) => file.webkitRelativePath || file.name) }));
    }}>
      <label>
        <small>Choose files</small>
        <input name={name} type="file" accept=".pdf,.docx" multiple />
      </label>
      <label>
        <small>Choose folder</small>
        <input
          name={name}
          type="file"
          accept=".pdf,.docx"
          multiple
          ref={(node) => {
            if (node) {
              node.setAttribute("webkitdirectory", "");
              node.setAttribute("directory", "");
            }
          }}
        />
      </label>
      {Object.values(selected).flat().length > 0 && <ul className="selected-upload-files" aria-label="Selected files" aria-live="polite">{Object.entries(selected).flatMap(([group, files]) => files.map((filename, index) => <li key={`${group}-${index}`}>{filename}</li>))}</ul>}
    </div>
  );
}

function ImportIssues({ job, stage }: { job: ImportJob; stage: "extraction" | "generation" }) {
  const [expanded, setExpanded] = useState(false), [page, setPage] = useState(1), [report, setReport] = useState<any>(null), [error, setError] = useState("");
  const total = stage === "extraction" ? job.errors_total ?? job.errors?.length ?? 0 : job.meta?.solution_generation?.failed ?? job.meta?.solution_generation?.errors?.length ?? 0;
  useEffect(() => {
    if (!expanded || !job.bank_id) return;
    const controller = new AbortController(); setError(""); setReport(null);
    api(`/admin/question-banks/${job.bank_id}/imports/${job.id}/issues?stage=${stage}&page=${page}&page_size=20`, { signal: controller.signal }).then(setReport).catch((error) => { if (error.name !== "AbortError") setError(error.message); });
    return () => controller.abort();
  }, [expanded, job.bank_id, job.id, stage, page, total]);
  if (!total) return null;
  const entries = report?.items || [];
  return <details className="import-error-log" onToggle={(event) => { if (event.target === event.currentTarget) setExpanded(event.currentTarget.open); }}>
    <summary>{stage === "extraction" ? "Extraction issues" : "Generation failures"} ({total})</summary>
    {error ? <Notice kind="error">{error}</Notice> : !report ? <p role="status">Loading diagnostics…</p> : <>
      <p>Showing {entries.length ? (page - 1) * report.page_size + 1 : 0}–{Math.min(page * report.page_size, report.available_total)} of {report.total}. {report.legacy_logs_missing ? `${report.total - report.available_total} older messages were not retained by the previous worker.` : ""}</p>
      <ul tabIndex={0} aria-label={`${stage} issue details`}>{entries.map((entry: any, index: number) => <li key={`${entry.question_id || entry.page || "job"}-${index}`}>{entry.question_id && <a href={bankUrl({ bankId: job.bank_id!, documentId: job.id, questionId: entry.question_id })}>Question {entry.source_number || entry.question_id}</a>}{entry.page ? ` Page ${entry.page}${entry.end_page && entry.end_page !== entry.page ? `–${entry.end_page}` : ""}: ` : " "}{entry.message}</li>)}</ul>
      <div className="diagnostic-pagination"><button type="button" className="secondary-action" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous issues</button><button type="button" className="secondary-action" disabled={!report.has_more} onClick={() => setPage(page + 1)}>Next issues</button></div>
    </>}
  </details>;
}

function BankImportStatus({
  jobs,
  onRetry,
  onPause,
  onResume,
  onCancel,
  onDelete,
  onReextract,
  onInspect,
  onPublish = () => {},
  onGenerate,
  onApproveGenerated,
}: {
  jobs?: ImportJob[];
  onRetry: (job: ImportJob) => void;
  onPause: (job: ImportJob) => void;
  onResume: (job: ImportJob) => void;
  onCancel: (job: ImportJob) => void;
  onDelete: (job: ImportJob) => void;
  onReextract: (job: ImportJob) => void;
  onInspect: (job: ImportJob) => void;
  onPublish?: (job: ImportJob) => void;
  onGenerate: (job: ImportJob) => void;
  onApproveGenerated: (job: ImportJob) => void;
}) {
  const [query,setQuery]=useState(()=>locationParam("source_q")), [status,setStatus]=useState(()=>locationParam("source_state","all")), [subjectFilter,setSubjectFilter]=useState(()=>locationParam("source_subject","all")), [sort,setSort]=useState("source"), [order,setOrder]=useState<string[]>([]);
  const subjects=Array.from(new Set((jobs||[]).map(job=>job.meta?.subject||"Unclassified")));
  const refreshOrder=(value=sort)=>{
    setSort(value);
    const sorted=[...(jobs||[])].sort((a,b)=>value==="filename"?(a.filename||"").localeCompare(b.filename||""):value==="issues"?(b.readiness?.needs_attention||0)-(a.readiness?.needs_attention||0):value==="activity"?String(b.lifecycle?.last_activity_at||"").localeCompare(String(a.lifecycle?.last_activity_at||"")):0);
    setOrder(sorted.map(job=>job.id));
  };
  const visible=(jobs||[]).filter(job=>(job.filename||"").toLowerCase().includes(query.trim().toLowerCase())&&(subjectFilter==="all"||(job.meta?.subject||"Unclassified")===subjectFilter)&&(status==="all"||(status==="attention"?(job.status==="failed"||Boolean(job.readiness?.needs_attention)||Boolean(job.meta?.solution_generation?.failed)||Boolean(job.errors_total)):status==="active"?["queued","running","pause_requested","cancellation_requested"].includes(job.lifecycle?.state||""):job.lifecycle?.state===status)));
  if(order.length)visible.sort((a,b)=>{const left=order.indexOf(a.id),right=order.indexOf(b.id);return(left<0?order.length:left)-(right<0?order.length:right);});
  const groups = visible.reduce<Record<string, ImportJob[]>>(
    (all, job) => {
      const subject = job.meta?.subject || "Unclassified";
      (all[subject] ||= []).push(job);
      return all;
    },
    {},
  );
  const allJobs = jobs || [],
    failed = allJobs.filter((job) => job.status === "failed" || Boolean(job.readiness?.needs_attention) || Boolean(job.errors_total) || Boolean(job.meta?.solution_generation?.failed)).length,
    active = allJobs.filter(
      (job) => ["queued", "running", "pause_requested", "cancellation_requested"].includes(job.lifecycle?.state || ""),
    ).length;
  return (
    <Card className="import-dashboard">
      <div className="import-dashboard-head">
        <div>
          <p className="eyebrow">SOURCE LIBRARY</p>
          <h2>Document imports</h2>
          <p>
            Monitor extraction, resolve source issues, and keep the bank ready
            to publish.
          </p>
        </div>
        <div className="import-summary">
          <button type="button" className="secondary-action" onClick={()=>setStatus("all")}><b>{allJobs.length}</b> documents</button>
          <button type="button" className="secondary-action" onClick={()=>setStatus("attention")}><b>{failed}</b> documents need attention</button>
          <button type="button" className="secondary-action" onClick={()=>setStatus("active")}><b>{active}</b> in progress</button>
        </div>
      </div>
      <div className="import-monitor-filters">
        <label>Find source<input type="search" value={query} onChange={event=>{setQuery(event.target.value);replaceLocationParams({source_q:event.target.value});}} placeholder="Filename"/></label>
        <label>Subject<select value={subjectFilter} onChange={event=>{setSubjectFilter(event.target.value);replaceLocationParams({source_subject:event.target.value});}}><option value="all">All subjects</option>{subjects.map(subject=><option key={subject}>{subject}</option>)}</select></label>
        <label>State<select value={status} onChange={event=>{setStatus(event.target.value);replaceLocationParams({source_state:event.target.value});}}>{["all","attention","active","queued","running","pause_requested","paused","cancellation_requested","cancelled","completed","failed"].map(value=><option key={value} value={value}>{value.replaceAll("_"," ")}</option>)}</select></label>
        <label>Order<select value={sort} onChange={event=>refreshOrder(event.target.value)}><option value="source">Source order</option><option value="filename">Filename</option><option value="activity">Last activity</option><option value="issues">Questions needing attention</option></select></label>
        <button type="button" className="secondary-action" onClick={()=>refreshOrder()}>Refresh ordering</button>
        <small>{visible.length} matching documents. Status counts can overlap; updates preserve the chosen ordering.</small>
      </div>
      {Object.keys(groups).length ? (
        Object.entries(groups).map(([subject, items]) => (
          <section className="import-subject" key={subject}>
            <div className="import-subject-head">
              <h3>{subject}</h3>
              <span>
                {items.length} document{items.length === 1 ? "" : "s"}
              </span>
            </div>
            {items.map((job) => {
              const generation = job.meta?.solution_generation;
              const generationActive = generation?.status === "queued" || generation?.status === "running";
              const generationComplete = generation?.status === "completed" || generation?.status === "completed_with_errors";
              const state = job.lifecycle?.state || (job.status === "processing" ? "running" : job.status === "uploaded" ? "queued" : job.status);
              const paused = state === "paused",
                running = ["queued", "running", "pause_requested", "cancellation_requested"].includes(state),
                pageProgress = job.meta?.total_pages
                  ? `${job.meta.processed_pages || 0}/${job.meta.total_pages} pages checked`
                  : "";
              const failedJob = job.status === "failed";
              // A pause request is durable but the worker may still be
              // finishing its in-flight page.  Only offer destructive/reset
              // actions after it has written the paused checkpoint.
              const canManageStoredOutput =
                !running || (paused && job.status === "uploaded");
              return (
                <article
                  className={`import-item ${failedJob ? "has-error" : ""}`}
                  key={job.id}
                >
                  <div className="import-file">
                    <span
                      className={`import-file-icon ${failedJob ? "error" : ""}`}
                      aria-hidden="true"
                    >
                      {failedJob ? "!" : "↗"}
                    </span>
                    <div>
                      <button type="button" className="import-file-link" onClick={() => onInspect(job)}>{job.filename || "Untitled import"}</button>
                      <small>
                        {failedJob
                          ? "Extraction stopped"
                          : job.meta?.phase || "Waiting to start"}
                        {job.question_count !== undefined
                          ? ` · ${job.question_count} questions`
                          : job.meta?.question_count !== undefined
                            ? ` · ${job.meta.question_count} questions found`
                            : ""}
                        {job.meta?.flagged_count
                          ? ` · ${job.meta.flagged_count} flagged`
                          : ""}
                        {job.meta?.skipped_count
                          ? ` · ${job.meta.skipped_count} skipped`
                          : ""}
                        {pageProgress ? ` · ${pageProgress}` : ""}
                      </small>
                      {job.error && (
                        <p className="import-error">
                          <strong>Action needed</strong>
                          {job.error}
                        </p>
                      )}
                      <ImportIssues job={job} stage="extraction" />
                      {job.lifecycle?.heartbeat_at && <small>Worker last active: {fmtDate(job.lifecycle.heartbeat_at)}</small>}
                      {generation && (
                        <details className="generation-progress">
                          <summary>Solutions: {generation.failed ? `${generation.failed} failed` : generation.status === "failed" ? "generation failed" : generationActive ? "in progress" : "generation complete"} · {generation.processed || 0}/{generation.total || 0} attempted</summary>
                          <div><span>{generation.phase || "Solution generation"}</span></div>
                          <progress aria-label={`Solution generation for ${job.filename || "document"}`} max={Math.max(1, generation.total || 1)} value={generation.processed || 0} />
                          <small>{generation.succeeded || 0} generated · {generation.failed || 0} failed{generation.approved ? ` · ${generation.approved} approved` : ""}</small>
                          {generation.error && <p className="import-error">{generation.error}</p>}
                          <ImportIssues job={job} stage="generation" />
                        </details>
                      )}
                    </div>
                  </div>
                  <div className="import-controls">
                    <span
                      className={`badge ${job.status}`}
                    >
                      {generation?.failed && state === "completed" ? "Completed with errors" : state.replaceAll("_", " ")}
                    </span>
                    {job.published && <span className="badge published">Published</span>}
                    <button
                      type="button"
                      className="import-primary-action"
                      data-import-review={job.id}
                      onClick={() => onInspect(job)}
                    >
                      {job.status === "review" ? "Review" : "Inspect"}
                    </button>
                    <details className="import-secondary-actions">
                      <summary>More actions</summary>
                      <div className="import-secondary-action-list">
                        {failedJob && !job.meta?.cancel_requested && (
                          <button type="button" onClick={() => onRetry(job)}>
                            Retry import
                          </button>
                        )}
                        {!job.published && !running && Boolean(job.generation_eligible_count) && !generationActive && (
                          <button type="button" onClick={() => onGenerate(job)}>
                            {generation?.status === "failed" || generation?.status === "completed_with_errors" ? "Retry missing generation" : "Generate missing answers & solutions"}
                          </button>
                        )}
                        {!job.published && generationComplete && Boolean(generation?.succeeded) && !generation?.approved_at && (
                          <button type="button" onClick={() => onApproveGenerated(job)}>Approve all generated</button>
                        )}
                        {!job.published && Boolean(readyQuestionCount(job)) && (
                          <button type="button" onClick={() => onPublish(job)}>
                            {job.status === "approved" ? "Confirm & publish" : "Publish extracted questions"}
                          </button>
                        )}
                        {["queued", "running"].includes(state) && (
                          <button
                            type="button"
                            className="quiet-action"
                            onClick={() => onPause(job)}
                          >
                            Pause
                          </button>
                        )}
                        {paused && (
                          <button
                            type="button"
                            className="quiet-action"
                            onClick={() => onResume(job)}
                          >
                            Resume
                          </button>
                        )}
                        {["queued", "running", "pause_requested", "paused"].includes(state) && (
                          <button
                            type="button"
                            className="icon-danger"
                            title="Stop import"
                            aria-label={`Stop ${job.filename || "import"}`}
                            onClick={() => onCancel(job)}
                          >
                            ×
                          </button>
                        )}
                        {canManageStoredOutput && !job.published && (
                          <button
                            type="button"
                            className="secondary-action"
                            title="Clear this material's extracted questions and run the stored file through ingestion again"
                            onClick={() => onReextract(job)}
                          >
                            Re-extract material
                          </button>
                        )}
                        <button
                            type="button"
                            className="text-danger"
                            title={job.meta?.delete_requested ? "Deletion will complete after the current page batch" : "Delete this source material and every extracted question"}
                            aria-label={`Delete ${job.filename || "import"}`}
                            disabled={Boolean(job.meta?.delete_requested)}
                            onClick={() => onDelete(job)}
                          >
                            {job.meta?.delete_requested ? "Deleting…" : "Delete material"}
                        </button>
                      </div>
                    </details>
                  </div>
                </article>
              );
            })}
          </section>
        ))
      ) : (
        <div className="empty-imports">
          <span>↑</span>
          <div>
            <b>{allJobs.length ? "No documents match these filters" : "No source documents yet"}</b>
            <p>{allJobs.length ? "Change the filename, subject or state filter." : "Open Add sources to upload a question document."}</p>
          </div>
        </div>
      )}
    </Card>
  );
}

function QuestionBankManager({
  notify: reportGlobal,
}: {
  notify: (message: string) => void;
}) {
  const [operationMessage,setOperationMessage]=useState("");
  const notify=(message:string)=>{setOperationMessage(message);reportGlobal(message);};
  const [banks, setBanks] = useState<Bank[]>([]),
    [bankQuery, setBankQuery] = useState(""),
    [bankStatus, setBankStatus] = useState("all"),
    [detail, setDetail] = useState<Bank | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState(false),
    [editingQuestion, setEditingQuestion] = useState<Question | null>(null),
    [figureEditing, setFigureEditing] = useState(false),
    [figureRevalidation, setFigureRevalidation] = useState<any>(null),
    [selectedImport, setSelectedImport] = useState<ImportJob | null>(null),
    [selectedQuestions, setSelectedQuestions] = useState<Question[] | null>(
      null,
    ),
    [uploadProgress, setUploadProgress] = useState<{
      loaded: number;
      total: number;
    } | null>(null);
  const uploadFormRef=useRef<HTMLFormElement>(null);
  const [uploadConfig,setUploadConfig]=useState<UploadConfig|null>(null), [uploadReview,setUploadReview]=useState<ReturnType<typeof reviewUploads>>([]), [uploadOutcomes,setUploadOutcomes]=useState<any[]>([]);
  useEffect(()=>{api("/admin/import-config").then(setUploadConfig).catch((error)=>setError(error.message));},[]);
  const reviewUploadForm=(form:HTMLFormElement)=>{
    if(!uploadConfig)return;
    const files=(name:string)=>Array.from(form.querySelectorAll<HTMLInputElement>(`input[name="${name}"][type="file"]`)).flatMap(input=>Array.from(input.files||[]));
    setUploadReview(reviewUploads(files("question_files"),files("answer_files"),uploadConfig));setUploadOutcomes([]);
  };
  const [bankPickerOpen,setBankPickerOpen]=useState(false);
  const [removedBanks,setRemovedBanks]=useState<Bank[]>([]);
  const showRemoved=async()=>{try{setRemovedBanks((await api("/admin/question-banks?include_deleted=true")).filter((bank:Bank)=>bank.status==="deleted"));}catch(error:any){setError(error.message);}};
  const restoreBank=async(bank:Bank)=>{if(!await confirmApp(`Restore ${bank.name} (${bank.id}) with its prior visibility? Content readiness remains enforced.`))return;try{await api(`/admin/question-banks/${bank.id}/restore`,{method:"POST"});await load();await showRemoved();notify("Bank restored. Content readiness checks remain in effect.");}catch(error:any){setError(error.message);}};
  const [workspace,setWorkspace]=useState<"add"|"monitor"|"review">(()=>["add","review"].includes(locationParam("task"))?locationParam("task") as "add"|"review":"monitor");
  useEffect(()=>{if(uploadConfig&&uploadFormRef.current)reviewUploadForm(uploadFormRef.current);},[uploadConfig,workspace]);
  const bankRequests = useRef(new LatestRequest()), documentRequests = useRef(new LatestRequest());
  const activeBank = useRef<string | null>(null), activeDocument = useRef<string | null>(null);
  const editingQuestionRef=useRef(editingQuestion);editingQuestionRef.current=editingQuestion;
  const dirtyQuestion = useRef(false), lastUrl = useRef(""), restoreSequence = useRef(0);
  const [loadingBank, setLoadingBank] = useState(false), [stale, setStale] = useState(false), [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const canLeave = () => !dirtyQuestion.current ? Promise.resolve(true) : confirmApp("Discard unsaved question edits?");
  const navigateTo = (documentId?: string, questionId?: string, replace = false) => {
    if (!activeBank.current) return;
    const url = bankUrl({ bankId: activeBank.current, documentId, questionId }) + window.location.search;
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
    lastUrl.current = url;
  };
  const load = async () => setBanks(await api("/admin/question-banks"));
  const open = async (id: string, updateUrl = true) => {
    const changing = activeBank.current !== id;
    if (changing && !await canLeave()) return;
    const request = bankRequests.current.start(id);
    activeBank.current = id;
    setError(""); setLoadingBank(true); setStale(false);
    if (changing) {
      documentRequests.current.cancel(); activeDocument.current = null; dirtyQuestion.current = false;
      setDetail(null); setSelectedImport(null); setSelectedQuestions(null); setEditingQuestion(null); setFigureRevalidation(null);setFigureEditing(false);
      if (updateUrl) navigateTo();
    }
    try {
      const bank = await api(`/admin/question-banks/${id}`, { signal: request.signal });
      if (!request.current()) return;
      setDetail(bank); setLastUpdated(new Date().toISOString()); setEditing(false);
      return bank;
    } catch (error: any) {
      if (request.current() && error.name !== "AbortError") { setError(error.message); setStale(!changing); }
    } finally { if (request.current()) setLoadingBank(false); }
  };
  const restoreLocation = async () => {
    if (!await canLeave()) { window.history.pushState({}, "", lastUrl.current); return; }
    const location = readBankLocation(window.location.pathname, window.location.hash);
    if (!location) { documentRequests.current.cancel(); setSelectedImport(null); setSelectedQuestions(null); setEditingQuestion(null); return; }
    dirtyQuestion.current = false;setFigureEditing(false);
    if(location.documentId!==activeDocument.current){setSelectedImport(null);setEditingQuestion(null);}
    const sequence = ++restoreSequence.current;
    const bank = await open(location.bankId, false);
    if (!bank || sequence !== restoreSequence.current || activeBank.current !== location.bankId) return;
    const documentRequest = documentRequests.current.start(`${location.bankId}/${location.documentId || ""}`);
    activeDocument.current = location.documentId || null;
    lastUrl.current = window.location.pathname + window.location.search;
    if (!location.documentId) {
      setSelectedImport(null);setSelectedQuestions(null);
      if(location.questionId){setWorkspace("review");try{const question=await api(`/admin/question-banks/${location.bankId}/questions/${location.questionId}`,{signal:documentRequest.signal});if(documentRequest.current())setEditingQuestion(question);}catch(error:any){if(documentRequest.current()&&error.name!=="AbortError")setError(error.message);}}
      else setEditingQuestion(null);
      return;
    }
    try {
      const report = await api(`/admin/question-banks/${location.bankId}/imports/${location.documentId}`, { signal: documentRequest.signal });
      if (!documentRequest.current() || activeBank.current !== location.bankId) return;
      setSelectedImport(report); setSelectedQuestions(report.questions || []);
      const selected = location.questionId ? await api(`/admin/question-banks/${location.bankId}/imports/${location.documentId}/questions/${location.questionId}`, { signal: documentRequest.signal }) : null;
      if (documentRequest.current()) setEditingQuestion(selected);
    } catch (error: any) { if (documentRequest.current() && error.name !== "AbortError") setError(error.message); }
  };
  useEffect(() => {
    load().catch((error) => setError(error.message));
    void restoreLocation();
    const navigate = () => { void restoreLocation(); };
    const leaving = (event: BeforeUnloadEvent) => { if (dirtyQuestion.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("popstate", navigate); window.addEventListener("hashchange", navigate); window.addEventListener("beforeunload", leaving);
    return () => { bankRequests.current.cancel(); documentRequests.current.cancel(); window.removeEventListener("popstate", navigate); window.removeEventListener("hashchange", navigate); window.removeEventListener("beforeunload", leaving); };
  }, []);
  useEffect(() => {
    const bankId = detail?.id;
    const active = detail?.imports?.some((job) => ["queued", "running", "pause_requested", "cancellation_requested"].includes(job.lifecycle?.state || "") || ["queued", "running"].includes(job.meta?.solution_generation?.status || ""));
    if (!bankId || !active || figureEditing || loadingBank) return;
    let cancelled = false, inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return; inFlight = true;
      try {
        const bank = await api(`/admin/question-banks/${bankId}`);
        if (!cancelled && activeBank.current === bankId) { setDetail(bank); setStale(false); setLastUpdated(new Date().toISOString()); }
        const current=editingQuestionRef.current;
        if(!cancelled&&activeBank.current===bankId&&current&&!dirtyQuestion.current&&["queued","running"].includes(current.interaction?.solution_generation?.status||"")){
          const refreshed=await api(`/admin/question-banks/${bankId}/questions/${current.id}`);
          if(!cancelled&&activeBank.current===bankId&&editingQuestionRef.current?.id===current.id&&!dirtyQuestion.current)setEditingQuestion(refreshed);
        }
      } catch (error: any) { if (!cancelled && activeBank.current === bankId) { setStale(true); setError(error.message); } }
      finally { inFlight = false; }
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [detail?.id, detail?.imports, figureEditing, loadingBank]);
  useEffect(() => {
    const bankId = detail?.id, documentId = selectedImport?.id;
    const active = selectedImport && (["queued", "running", "pause_requested", "cancellation_requested"].includes(selectedImport.lifecycle?.state || "") || ["queued", "running"].includes(selectedImport.meta?.solution_generation?.status || ""));
    if (!bankId || !documentId || !active || figureEditing) return;
    let cancelled = false, inFlight = false;
    const timer = window.setInterval(async () => {
      if (inFlight) return; inFlight = true;
      try {
        const report = await api(`/admin/question-banks/${bankId}/imports/${documentId}`);
        if (!cancelled && activeBank.current === bankId && activeDocument.current === documentId && !dirtyQuestion.current) { setSelectedImport(report); setSelectedQuestions(report.questions || []); }
      } catch (error: any) { if (!cancelled && activeDocument.current === documentId) { setStale(true); setError(error.message); } }
      finally { inFlight = false; }
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [detail?.id, selectedImport?.id, selectedImport?.lifecycle?.state, selectedImport?.meta?.solution_generation?.status, figureEditing]);
  useEffect(() => {
    const bankId = detail?.id;
    if (!bankId || !figureRevalidation || !["queued", "running"].includes(figureRevalidation.status)) return;
    let cancelled = false;
    const timer = window.setInterval(() => { api(`/admin/question-banks/${bankId}/figure-revalidation`).then((run) => { if (!cancelled && activeBank.current === bankId) setFigureRevalidation(run); }).catch((error) => { if (!cancelled && activeBank.current === bankId) setError(error.message); }); }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [detail?.id, figureRevalidation?.status]);
  const editQuestion = async (question: Question | null) => {
    if (!await canLeave()) return;
    dirtyQuestion.current = false; setFigureEditing(false); setEditingQuestion(question);
    navigateTo(activeDocument.current || undefined, question?.id);
  };
  const closeDocument = async () => {
    if (!await canLeave()) return;
    dirtyQuestion.current = false; setFigureEditing(false); activeDocument.current = null; documentRequests.current.cancel();
    setSelectedImport(null); setSelectedQuestions(null); setEditingQuestion(null); navigateTo();
  };
  const revalidateFigures = async () => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true); setError("");
    try {
      const run = await api(`/admin/question-banks/${detail.id}/figure-revalidation`, { method: "POST" });
      setFigureRevalidation(run);
      notify(`Figure revalidation queued (${String(run.run_id).slice(0, 8)}).`);
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    setBusy(true);
    setError("");
    try {
      const bank: Bank = await api("/admin/question-banks", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.reset();
      await load();
      await open(bank.id);
      notify("Draft bank created.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const saveBank = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!detail || activeBank.current !== detail.id) return;
    const form = e.currentTarget;
    setBusy(true);
    try {
      await api(`/admin/question-banks/${detail.id}`, {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      await load();
      if (activeBank.current === detail.id) await open(detail.id);
      notify("Bank details saved.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const upload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!detail || activeBank.current !== detail.id) return;
    const form = e.currentTarget;
    if (!uploadConfig || uploadReview.some(file=>file.errors.length)) { setError("Correct the file validation errors before starting extraction."); return; }
    setBusy(true);
    setUploadProgress({ loaded: 0, total: 0 });
    try {
      const result = await uploadBankImport(detail.id, bankImportForm(form), (loaded, total) =>
        setUploadProgress({ loaded, total }),
      );
      setUploadOutcomes(result.outcomes || []);
      const failed = (result.outcomes || []).some((outcome:any)=>outcome.status==="failed");
      if (!failed) { form.reset(); setUploadReview([]); setWorkspace("monitor"); }
      if (activeBank.current === detail.id) await open(detail.id);
      await load();
      notify(failed ? "Some files failed. Successful sources were retained; correct and retry the failed files." : "Sources queued for extraction.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  };
  const retryImport = async (job: ImportJob) => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true);
    setError("");
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${job.id}/retry`, {
        method: "POST",
      });
      if (activeBank.current === detail.id) await open(detail.id);
      notify(`${job.filename || "Source"} queued for retry.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const importAction = async (
    job: ImportJob,
    action: "pause" | "resume" | "cancel",
  ) => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true);
    try {
      await api(
        `/admin/question-banks/${detail.id}/imports/${job.id}/${action}`,
        { method: "POST" },
      );
      if (activeBank.current === detail.id) await open(detail.id);
      notify(`${job.filename} ${action} requested.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const deleteImport = async (job: ImportJob) => {
    if (
      !detail || activeBank.current !== detail.id ||
      !await confirmApp(
        `Permanently delete ${job.filename} and its extracted questions from ${detail.name}? Assessment references are protected. Recovery requires uploading the original source again.`,
      )
    )
      return;
    setBusy(true);
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${job.id}`, {
        method: "DELETE",
      });
      setSelectedImport(null);
      if (activeBank.current === detail.id) await open(detail.id);
      await load();
      notify(job.status === "processing" || job.status === "uploaded" ? "Deletion requested. The material will disappear after its current page batch stops." : "Source removed from this bank.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const reextractImport = async (job: ImportJob) => {
    if (
      !detail || activeBank.current !== detail.id ||
      !await confirmApp(
        `Re-extract ${job.filename}? Its current extracted questions will be replaced using the same stored source file.`,
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${job.id}/reextract`, {
        method: "POST",
      });
      setSelectedImport(null);
      if (activeBank.current === detail.id) await open(detail.id);
      notify(`${job.filename || "Source"} queued for clean re-extraction.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const publish = async () => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true);
    try {
      const result = await publishWithPreview(`/admin/question-banks/${detail.id}`, detail.name);
      if (!result) return;
      if (activeBank.current === detail.id) await open(detail.id);
      await load();
      notify(publicationMessage(result));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const publishImport = async (job: ImportJob) => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true);
    try {
      const result = await publishWithPreview(`/admin/question-banks/${detail.id}/imports/${job.id}`, job.filename || "this source document");
      if (!result) return;
      if (activeBank.current === detail.id) await open(detail.id);
      await load();
      if(activeBank.current===detail.id&&activeDocument.current===job.id)setSelectedImport(await api(`/admin/question-banks/${detail.id}/imports/${job.id}`));
      notify(publicationMessage(result));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const generateImportSolutions = async (job: ImportJob) => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true); setError("");
    try {
      const state = await api(`/admin/question-banks/${detail.id}/imports/${job.id}/solution-generation`, { method: "POST" });
      setSelectedImport((current) => current?.id === job.id ? { ...current, meta: { ...current.meta, solution_generation: state } } : current);
      if (activeBank.current === detail.id) await open(detail.id);
      notify(`${job.filename || "Source document"} queued for answer and solution generation.`);
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const approveGeneratedSolutions = async (job: ImportJob) => {
    if (!detail || !job.meta?.solution_generation?.run_id) return;
    if (!await confirmApp("Approve every successfully generated question in this run? Questions with separate extraction, taxonomy, or visual issues will stay in review.")) return;
    setBusy(true); setError("");
    try {
      const runId = job.meta.solution_generation.run_id;
      const result = await api(`/admin/question-banks/${detail.id}/imports/${job.id}/solution-generation/${runId}/approve`, { method: "POST" });
      if (activeBank.current === detail.id) await open(detail.id); await load();
      notify(`${result.approved_count || 0} generated questions approved.`);
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const revision = async () => {
    if (!detail || activeBank.current !== detail.id) return;
    setBusy(true);
    try {
      const bank: Bank = await api(
        `/admin/question-banks/${detail.id}/revision`,
        { method: "POST" },
      );
      await load();
      await open(bank.id);
      notify("Editable draft revision created.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (
      !detail || activeBank.current !== detail.id ||
      !await confirmApp(`Hide ${detail.name} from the library? Content and assessment history are preserved. Restore it from Removed banks when needed.`)
    )
      return;
    setBusy(true);
    try {
      await api(`/admin/question-banks/${detail.id}`, { method: "DELETE" });
      setDetail(null);
      await load();
      notify("Question bank removed from the library.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const inspectImport = async (job: ImportJob) => {
    if (!detail || activeBank.current !== detail.id || !await canLeave()) return;
    const bankId = detail.id;
    const request = documentRequests.current.start(`${bankId}/${job.id}`);
    activeDocument.current = job.id; dirtyQuestion.current = false; setBusy(true); setError("");
    try {
      const report = await api(`/admin/question-banks/${bankId}/imports/${job.id}`, { signal: request.signal });
      if (!request.current() || activeBank.current !== bankId) return;
      setSelectedImport(report); setSelectedQuestions(report.questions || []); setEditingQuestion(null); navigateTo(job.id);
    } catch (error: any) { if (request.current() && error.name !== "AbortError") setError(error.message); }
    finally { if (request.current()) setBusy(false); }
  };
  const finishQuestionReview = async () => {
    dirtyQuestion.current=false;setFigureEditing(false);
    if(!detail||activeBank.current!==detail.id)return;
    const questionId=editingQuestion?.id;
    await open(detail.id,false);
    if(activeBank.current!==detail.id)return;
    if(selectedImport){const report=await api(`/admin/question-banks/${detail.id}/imports/${selectedImport.id}`);setSelectedImport(report);}
    if(questionId){const updated=await api(`/admin/question-banks/${detail.id}/questions/${questionId}`);if(activeBank.current===detail.id)setEditingQuestion(updated);}
  };
  const deleteQuestion = async (question: Question) => {
    if (!detail || !selectedImport || !await confirmApp(`Delete ${questionNumber(question.source_number)} as an invalid extracted question? This cannot be undone.`)) return;
    setBusy(true); setError("");
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${selectedImport.id}/questions/${question.id}`, { method: "DELETE" });
      setEditingQuestion(null);
      await finishQuestionReview();
      notify(`${questionNumber(question.source_number)} deleted from ${selectedImport.filename || "the source document"}.`);
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const visibleQuestions = selectedImport
    ? selectedQuestions
    : detail?.questions;
  const visibleBanks = banks.filter((bank) => {
    const matchesQuery = bank.name
      .toLowerCase()
      .includes(bankQuery.trim().toLowerCase());
    return matchesQuery && (bankStatus === "all" || bank.status === bankStatus);
  });
  const totalQuestionCount = banks.reduce(
    (total, bank) => total + bank.question_count,
    0,
  );
  return (
    <div className="page bank-manager">
      <p className="eyebrow">SUPER ADMIN · QUESTION BANKS</p>
      <h1>Question bank library</h1>
      {error && <Notice kind="error">{error}</Notice>}
      <details className="bank-picker" open={!detail || bankPickerOpen} onToggle={event => { if (detail) setBankPickerOpen(event.currentTarget.open); }}>
        <summary>{detail ? `Switch bank or create a new bank` : "Choose or create a bank"}</summary>
      <div className="bank-workspace">
        <Card className="new-bank-card">
          <div className="bank-workspace-heading">
            <span className="bank-workspace-icon">+</span>
            <h2>New bank</h2>
          </div>
          <form className="stacked-form" onSubmit={create}>
            <div className="new-bank-fields">
              <label>
                Name
                <input name="name" placeholder="e.g. Science Library 2024" required />
              </label>
              <label>
                Exam code <span className="optional">(optional)</span>
                <input name="exam_code" placeholder="JEE, NEET, etc." />
              </label>
            </div>
            <button disabled={busy}>Create draft</button>
          </form>
        </Card>
        <Card className="bank-navigator">
          <details><summary onClick={()=>void showRemoved()}>Removed banks and recovery</summary>{removedBanks.length?removedBanks.map(bank=><div key={bank.id}><span>{bank.name}</span><button type="button" className="secondary-action" onClick={()=>void restoreBank(bank)}>Restore {bank.name}</button></div>):<p>No removed banks found.</p>}</details>
          <div className="bank-navigator-header">
            <div className="bank-navigator-title">
              <h2>Banks</h2>
              <span>{banks.length} total</span>
              <span>{totalQuestionCount.toLocaleString()} questions</span>
            </div>
            <div className="bank-filters">
              <input
                aria-label="Search banks"
                type="search"
                value={bankQuery}
                onChange={(event) => setBankQuery(event.target.value)}
                placeholder="Search banks"
              />
              <select
                aria-label="Filter banks by status"
                value={bankStatus}
                onChange={(event) => setBankStatus(event.target.value)}
              >
                <option value="all">All status</option>
                <option value="published">Published</option>
                <option value="processing">Processing</option>
                <option value="draft">Draft</option>
              </select>
            </div>
          </div>
          <div className="bank-list-scroll" aria-label="Question banks">
            {visibleBanks.map((bank) => (
              <button
                type="button"
                className={`bank-choice-row ${detail?.id === bank.id ? "selected" : ""}`}
                key={bank.id}
                disabled={busy}
                aria-pressed={detail?.id === bank.id}
                onClick={() => { void open(bank.id).then(() => { if (activeBank.current === bank.id) setBankPickerOpen(false); }); }}
              >
                <span>
                  <b>{bank.name}</b>
                  <small>revision {bank.revision}</small>
                </span>
                <span className="bank-choice-meta">
                  <small>{bank.question_count.toLocaleString()} questions</small>
                  <span className={`badge ${bank.status}`}>{bank.status}</span>
                </span>
              </button>
            ))}
            {!visibleBanks.length && (
              <div className="bank-list-empty">
                <b>{banks.length ? "No banks match this filter." : "Start your first question bank."}</b>
                <p>{banks.length ? "Try a different name or status." : "Create a draft, add a source document, then review and publish questions when they are ready."}</p>
              </div>
            )}
          </div>
        </Card>
      </div>
      </details>
      <section className="bank-detail-workspace" aria-busy={loadingBank}>
          {loadingBank && <Notice>Loading the requested bank…</Notice>}
          {stale && <Notice kind="error">Refresh failed. Displayed data is stale. <button type="button" className="secondary-action" onClick={() => activeBank.current && open(activeBank.current)}>Retry refresh</button></Notice>}
          {lastUpdated && detail && <small>Last successful update: {new Date(lastUpdated).toLocaleTimeString()}</small>}
          {detail ? (
            <>
              <Card>
                <div className="section-heading">
                  <div>
                    <h2>{detail.name}</h2>
                    <p>
                      {detail.exam_code} · {detail.status} · revision{" "}
                      {detail.revision}
                    </p>
                  </div>
                  <div className="bank-actions">
                    {detail.status === "published" && <span className="badge published">Live</span>}
                    <button type="button" onClick={publish} disabled={busy}>Preview publication</button>
                    <button type="button" className="secondary-action" onClick={revalidateFigures} disabled={busy}>Revalidate figures</button>
                    <button
                      type="button"
                      className="secondary-action"
                      onClick={() => setEditing(!editing)}
                    >
                      Edit details
                    </button>
                    <button
                      type="button"
                      className="text-danger"
                      onClick={remove}
                      disabled={busy}
                    >
                      Delete
                    </button>
                  </div>
                </div>
                {figureRevalidation && <Notice kind={figureRevalidation.status === "failed" ? "error" : "info"}>
                  Figure revalidation: {figureRevalidation.status}
                  {typeof figureRevalidation.processed === "number" ? ` · ${figureRevalidation.processed}/${figureRevalidation.total} checked` : ""}
                  {figureRevalidation.status === "completed" ? ` · ${figureRevalidation.cleared || 0} false flags cleared, ${figureRevalidation.needs_review || 0} need review.` : ""}
                  {figureRevalidation.report_key && <><span> </span><a href={`/api/admin/question-banks/${detail.id}/figure-revalidation/${figureRevalidation.run_id}/report`}>Download audit report</a></>}
                </Notice>}
                {editing && (
                  <form
                    className="stacked-form compact-form"
                    onSubmit={saveBank}
                  >
                    <label>
                      Name
                      <input name="name" defaultValue={detail.name} required />
                    </label>
                    <label>
                      Exam code
                      <input name="exam_code" defaultValue={detail.exam_code} />
                    </label>
                    <button disabled={busy}>Save details</button>
                  </form>
                )}
                <p>Each source document stays private until confirmed. Preview publication to see eligible, blocked and already published question counts. Only eligible questions become available for new tests.</p>
              </Card>
              {detail.readiness && <Notice>Full bank: {detail.readiness.total} questions · {detail.readiness.publishable} ready · {detail.readiness.blocked} with blocking content issues · {detail.readiness.needs_review} awaiting review (counts can overlap). <a href={`/api/admin/question-banks/${detail.id}/readiness-report`} target="_blank" rel="noreferrer">Open nonmutating readiness report</a></Notice>}
              <nav className="ingestion-task-tabs" aria-label="Bank workspace task">{([['monitor','Monitor imports'],['add','Add sources'],['review','Review library']] as const).map(([value,label])=><button type="button" key={value} className={workspace===value?"active":"secondary-action"} aria-current={workspace===value?"page":undefined} onClick={async()=>{if(await canLeave()){dirtyQuestion.current=false;setFigureEditing(false);setEditingQuestion(null);setWorkspace(value);replaceLocationParams({task:value});}}}>{label}</button>)}</nav>
              {workspace === "add" && <Card className="bank-upload-card">
                <h2>Upload source documents</h2>
                <form ref={uploadFormRef} onSubmit={upload} onChange={event=>reviewUploadForm(event.currentTarget)} className="stacked-form">
                  <label>
                    Subject
                    <input name="subject" placeholder="Physics" required />
                  </label>
                  <div className="upload-field">
                    <span>Question documents — files or a folder</span>
                    <FolderInput name="question_files" required />
                  </div>
                  <div className="upload-field">
                    <span>
                      Answer / solution documents — files or a folder{" "}
                      <span className="optional">(optional)</span>
                    </span>
                    <FolderInput name="answer_files" />
                  </div>
                  <small>
                    Select one folder to include every PDF/DOCX inside it, or
                    select multiple files. Pair question and solution files with
                    matching names; a combined paper can be uploaded as a
                    question document.
                  </small>
                  {uploadProgress && (
                    <small>
                      Uploading{" "}
                      {uploadProgress.total
                        ? `${Math.round((uploadProgress.loaded / uploadProgress.total) * 100)}%`
                        : "…"}
                    </small>
                  )}
                  {uploadConfig&&<small>Supported: {uploadConfig.extensions.map(extension=>extension.toUpperCase()).join(", ")} · up to {Math.round(uploadConfig.max_file_bytes/1024/1024)} MB per file. Question documents are required; answer documents are optional. Content duplicates are checked on the server.</small>}
                  {uploadReview.length>0&&<ul className="upload-preflight" aria-label="Upload validation">{uploadReview.map((file,index)=><li key={`${file.role}-${index}`}><b>{file.filename}</b> · {file.role} · {file.paired?`paired with ${file.paired}`:file.role==="question"?"no separate answer source":"answer source"}{file.duplicate?" · possible duplicate (server verifies content)":""}{file.errors.map(error=><span className="error-text" key={error}> · {error}</span>)}</li>)}</ul>}
                  {uploadOutcomes.length>0&&<ul className="upload-preflight" aria-label="Per-file upload outcomes">{uploadOutcomes.map((outcome,index)=><li key={index}><b>{outcome.filename}</b> · {outcome.status.replaceAll("_"," ")}{outcome.error?` · ${outcome.error}`:""}</li>)}</ul>}
                  <button disabled={busy||!uploadConfig||uploadReview.some(file=>file.errors.length)}>
                    {busy ? "Uploading…" : "Upload and start extraction"}
                  </button>
                </form>
              </Card>}
              {workspace === "monitor" && <BankImportStatus
                jobs={detail.imports}
                onRetry={retryImport}
                onPause={(job) => importAction(job, "pause")}
                onResume={(job) => importAction(job, "resume")}
                onCancel={(job) => importAction(job, "cancel")}
                onDelete={deleteImport}
                onReextract={reextractImport}
                onInspect={inspectImport}
                onPublish={publishImport}
                onGenerate={generateImportSolutions}
                onApproveGenerated={approveGeneratedSolutions}
              />}
              {selectedImport && (
                <DocumentImportPage
                  bankName={detail.name}
                  feedback={operationMessage}
                  operationError={error}
                  job={selectedImport}
                  questions={selectedQuestions || []}
                  activeQuestion={editingQuestion}
                  busy={busy}
                  close={closeDocument}
                  edit={editQuestion}
                  deleteQuestion={deleteQuestion}
                  publish={() => publishImport(selectedImport)}
                  generate={() => generateImportSolutions(selectedImport)}
                  approveGenerated={() => approveGeneratedSolutions(selectedImport)}
                  renderEditor={(question) => <BankQuestionEditor key={question.id} bankId={detail.id} question={question} done={finishQuestionReview} cancel={() => editQuestion(null)} onDirtyChange={(dirty) => { dirtyQuestion.current = dirty; }} onFigureActive={setFigureEditing} />}
                />
              )}
              {workspace === "review" && <Card><QuestionLibrary key={detail.id} bankId={detail.id} selectedQuestion={editingQuestion} api={api} edit={editQuestion} renderEditor={question=><BankQuestionEditor key={question.id} bankId={detail.id} question={question} done={finishQuestionReview} cancel={()=>editQuestion(null)} onDirtyChange={dirty=>{dirtyQuestion.current=dirty;}} onFigureActive={setFigureEditing}/>}/></Card>}
            </>
          ) : (
            <Card>Create or select a bank to begin.</Card>
          )}
      </section>
    </div>
  );
}

function DocumentImportPage({bankName,feedback,operationError,job,activeQuestion,busy,close,edit,deleteQuestion,publish,generate,approveGenerated,renderEditor}:{
  bankName:string; feedback?:string; operationError?:string; job:ImportJob; questions:Question[]; activeQuestion:Question|null; busy:boolean; close:()=>void; edit:(question:Question|null)=>void;
  deleteQuestion:(question:Question)=>void; publish:()=>void; generate:()=>void; approveGenerated:()=>void;
  renderEditor:(question:Question)=>React.ReactNode;
}) {
  type Filter = "all" | "attention" | "generated" | "published";
  const params = () => new URLSearchParams(window.location.search);
  const initialFilter = () => { const value = params().get("filter") || "all"; return ["all","attention","generated","published"].includes(value) ? value as Filter : "all"; };
  const [filter,setFilter]=useState<Filter>(initialFilter), [page,setPage]=useState(() => Math.max(1,Number(params().get("qpage")) || 1));
  const [queue,setQueue]=useState<any>(null), [loading,setLoading]=useState(false), [error,setError]=useState(""), [retry,setRetry]=useState(0);
  const [edge,setEdge]=useState<"first"|"last"|null>(null);
  const dialogFocus=useDialogFocus(true,close,()=>document.querySelector<HTMLButtonElement>(`[data-import-review="${job.id}"]`));
  const selectionRequests=useRef(new LatestRequest()), scrollContainer=useRef<HTMLDivElement>(null);
  const updateLocation=(nextFilter:Filter,nextPage:number)=>{const url=new URL(window.location.href);url.searchParams.set("filter",nextFilter);url.searchParams.set("qpage",String(nextPage));window.history.replaceState({},"",url.pathname+url.search);};
  const changeFilter=(value:Filter)=>{setFilter(value);setPage(1);updateLocation(value,1);};
  const changePage=(value:number)=>{setPage(value);updateLocation(filter,value);};
  const selectQuestion=async(id:string)=>{
    const request=selectionRequests.current.start(id);setLoading(true);setError("");
    try {const question=await api(`/admin/question-banks/${job.bank_id}/imports/${job.id}/questions/${id}`,{signal:request.signal});if(request.current())edit(question);}
    catch(error:any){if(request.current()&&error.name!=="AbortError")setError(error.message);}
    finally{if(request.current())setLoading(false);}
  };
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");
    api(`/admin/question-banks/${job.bank_id}/imports/${job.id}/questions?review_filter=${filter}&page=${page}&page_size=25`,{signal:controller.signal})
      .then((result)=>{setQueue(result);if(edge&&result.items.length){void selectQuestion(result.items[edge==="first"?0:result.items.length-1].id);setEdge(null);}})
      .catch((error)=>{if(error.name!=="AbortError")setError(error.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[job.id,filter,page,job.readiness?.publishable,job.readiness?.needs_attention,retry]);
  useEffect(()=>{
    const restore=()=>{setFilter(initialFilter());setPage(Math.max(1,Number(params().get("qpage"))||1));};window.addEventListener("popstate",restore);
    if(scrollContainer.current)scrollContainer.current.scrollTop=Number(window.sessionStorage.getItem(`document-scroll:${job.id}`)||0);
    return()=>{selectionRequests.current.cancel();window.removeEventListener("popstate",restore);};
  },[job.id]);
  const counts=queue?.counts||{}, items=queue?.items||[], selectedIndex=items.findIndex((item:any)=>item.id===activeQuestion?.id);
  const move=(direction:number)=>{const next=selectedIndex+direction;if(next>=0&&next<items.length){void selectQuestion(items[next].id);}else if(direction>0&&queue?.has_more){setEdge("first");changePage(page+1);}else if(direction<0&&page>1){setEdge("last");changePage(page-1);}};
  const generation=job.meta?.solution_generation, generationActive=["queued","running"].includes(generation?.status||"");
  return <div ref={element=>{scrollContainer.current=element;dialogFocus.current=element;}} tabIndex={-1} onScroll={(event)=>window.sessionStorage.setItem(`document-scroll:${job.id}`,String(event.currentTarget.scrollTop))} className="document-detail-page" role="dialog" aria-modal="true" aria-label={`Review ${job.filename || "source"}`}>
    <div className="document-detail-shell">
      <header className="document-detail-header">
        <button type="button" className="secondary-action" onClick={close}>← Back to documents</button>
        <div><p className="eyebrow">{bankName} · SOURCE REVIEW</p><h1>{job.filename}</h1><p>{job.meta?.subject||"Unclassified"} · {job.lifecycle?.state.replaceAll("_"," ")||job.status}</p></div>
        <div className="document-detail-actions">
          {!job.published&&Boolean(job.generation_eligible_count)&&!generationActive&&<button type="button" className="secondary-action" disabled={busy} onClick={generate}>Generate missing</button>}
          {!job.published&&generation&&["completed","completed_with_errors"].includes(generation.status)&&Boolean(generation.succeeded)&&!generation.approved_at&&<button type="button" className="secondary-action" disabled={busy} onClick={approveGenerated}>Review generated batch</button>}
          {!job.published&&Boolean(readyQuestionCount(job))&&<button type="button" disabled={busy} onClick={publish}>Preview publication ({readyQuestionCount(job)} ready)</button>}
          {job.published&&<span className="badge published">Published</span>}
        </div>
      </header>
      {feedback&&<Notice>{feedback}</Notice>}{operationError&&<Notice kind="error">{operationError}</Notice>}
      <section className="document-metrics"><span><b>{job.question_count||0}</b><small>questions in source</small></span><span><b>{readyQuestionCount(job)}</b><small>ready</small></span><span><b>{job.readiness?.needs_attention||0}</b><small>need attention</small></span><span><b>{generation?.succeeded||0}</b><small>generated</small></span><span><b>{generation?.failed||0}</b><small>generation failures</small></span></section>
      <nav className="document-filters" aria-label="Filter document questions">{([['all','All'],['attention','Needs attention'],['generated','Generated'],['published','Ready']] as const).map(([value,label])=><button type="button" key={value} className={filter===value?"active":""} onClick={()=>changeFilter(value)}>{label} {counts[value]||0}</button>)}</nav>
      <section className="document-issue-summary"><ImportIssues job={job} stage="extraction"/><ImportIssues job={job} stage="generation"/></section>
      {error&&<Notice kind="error">{error} <button type="button" onClick={()=>setRetry(retry+1)}>Retry</button></Notice>}
      <main className="bounded-review-workspace" aria-busy={loading}>
        <aside className="bounded-question-queue" aria-label="Question review queue">
          <p role="status">{loading?"Loading…":`${queue?.total||0} matching questions · page ${page}`}</p>
          {items.map((item:any)=><button type="button" key={item.id} disabled={loading} className={`queue-question ${activeQuestion?.id===item.id?"active":""}`} onClick={()=>void selectQuestion(item.id)}><b>{questionNumber(item.source_number)} · {item.kind.replaceAll("_"," ")}</b><span>{item.preview}</span><small>{item.readiness.publishable?"Ready":item.readiness.content_valid?"Awaiting review":"Needs repair"}{item.source_page?` · page ${item.source_page}`:""}</small></button>)}
          {!loading&&!items.length&&<p>{filter==="all"?"No questions have been extracted yet.":"No questions match this filter."}</p>}
          <div className="queue-pagination"><button type="button" className="secondary-action" disabled={page===1||loading} onClick={()=>changePage(page-1)}>Previous page</button><button type="button" className="secondary-action" disabled={!queue?.has_more||loading} onClick={()=>changePage(page+1)}>Next page</button></div>
        </aside>
        <section className="selected-question-detail" aria-label="Selected question">
          {activeQuestion?<><div className="selected-question-actions"><span>Question {activeQuestion.source_number||activeQuestion.id}</span><button type="button" className="secondary-action" disabled={loading||(selectedIndex<=0&&page===1)} onClick={()=>move(-1)}>Previous question</button><button type="button" className="secondary-action" disabled={loading||(selectedIndex===items.length-1&&!queue?.has_more)} onClick={()=>move(1)}>Next question</button><button type="button" className="text-danger" disabled={busy} onClick={()=>deleteQuestion(activeQuestion)}>Delete question</button></div>{renderEditor(activeQuestion)}</>:<p>Select a question to inspect its content, source and readiness. Full math content loads only for the selected question.</p>}
        </section>
      </main>
    </div>
  </div>;
}

function QuestionReviewRow({
  question,
  activeQuestion,
  edit,
  renderEditor,
}: {
  question: Question;
  activeQuestion: Question | null;
  edit: (question: Question) => void;
  renderEditor: (question: Question) => React.ReactNode;
}) {
  const issues = question.readiness?.blocking.map((reason) => reason.message) || question.interaction?.ingestion_issues || [];
  return <div className="document-question" key={question.id}>
    <article className="row">
      <div>
        <b>{questionNumber(question.source_number)} · {question.kind}</b>
        <small>Page {question.interaction?.source_page || "—"} · {issues.length ? `Flagged: ${issues.join(", ")}` : questionReadinessLabel(question)}</small>
      </div>
      <button type="button" className="secondary-action" onClick={() => edit(question)}>
        {activeQuestion?.id === question.id ? "Editing" : "View / edit"}
      </button>
    </article>
    {activeQuestion?.id === question.id && renderEditor(question)}
  </div>;
}

function TopicQuestionBrowser({
  questions,
  activeQuestion,
  edit,
  renderEditor,
}: {
  questions: Question[];
  activeQuestion: Question | null;
  edit: (question: Question) => void;
  renderEditor: (question: Question) => React.ReactNode;
}) {
  const tree = questions.reduce<Record<string, Record<string, Record<string, Question[]>>>>((all, question) => {
    const nodes = question.taxonomy || [];
    const subject = nodes.find((node) => node.kind === "subject")?.name || "Unclassified subject";
    const chapter = nodes.find((node) => node.kind === "chapter")?.name || "Unclassified chapter";
    const topic = nodes.find((node) => node.kind === "topic")?.name || "Unclassified topic";
    (((all[subject] ||= {})[chapter] ||= {})[topic] ||= []).push(question);
    return all;
  }, {});
  return (
    <section className="topic-question-browser">
      <div className="question-browser-heading"><div><p className="eyebrow">QUESTION LIBRARY</p><h3>Browse by chapter and topic</h3><p>Topics stay compact until you need to inspect their questions.</p></div><span>{questions.length} questions</span></div>
      {Object.entries(tree).map(([subject, chapters]) => <details className="taxonomy-subject" key={subject} open><summary><b>{subject}</b><small>{Object.values(chapters).reduce((sum, topics) => sum + Object.values(topics).reduce((count, items) => count + items.length, 0), 0)} questions</small></summary>{Object.entries(chapters).map(([chapter, topics]) => <details className="taxonomy-chapter" key={chapter} open><summary><b>{chapter}</b><small>{Object.values(topics).reduce((sum, items) => sum + items.length, 0)} questions</small></summary>{Object.entries(topics).map(([topic, topicQuestions]) => <details className="taxonomy-topic" key={topic} open={topicQuestions.some((question) => question.id === activeQuestion?.id)}><summary><span><b>{topic}</b><small>{topicQuestions.filter(questionNeedsAttention).length ? "Needs attention" : "Complete"}</small></span><strong>{topicQuestions.length}</strong></summary><div className="topic-question-list">{topicQuestions.map((question) => <QuestionReviewRow key={question.id} question={question} activeQuestion={activeQuestion} edit={edit} renderEditor={renderEditor} />)}</div></details>)}</details>)}</details>)}
    </section>
  );
}

function AttentionQuestionQueue({questions,activeQuestion,edit,renderEditor}:{questions:Question[];activeQuestion:Question|null;edit:(question:Question)=>void;renderEditor:(question:Question)=>React.ReactNode}){
 const attention=questions.filter(questionNeedsAttention);
 return <section className="attention-question-queue"><div className="question-browser-heading"><div><p className="eyebrow">REQUIRES REVIEW</p><h3>Questions needing attention</h3><p>Questions in the loaded library slice that need repair or review appear here.</p></div><span className={attention.length?"needs-attention":""}>{attention.length} items</span></div>{attention.length?<div className="document-question-list">{attention.map(question=><QuestionReviewRow key={question.id} question={question} activeQuestion={activeQuestion} edit={edit} renderEditor={renderEditor}/>)}</div>:<p className="queue-empty">No blocking issues or pending reviews in the loaded questions. Classification and other advisories are shown in each editor.</p>}</section>
}

function inferredChoiceFromSolution(question: Question) {
  if (
    question.answer ||
    !["single_choice", "assertion_reason"].includes(question.kind)
  )
    return question.answer;
  const match = String(question.solution_markdown || "").match(
    /(?:correct\s+(?:option|answer)|answer)\s*(?:is|:)?\s*\(?\s*([A-D])\s*\)?/i,
  );
  return match ? { option: match[1].toUpperCase().charCodeAt(0) - 65 } : null;
}
function sourceLabel(question: Question) {
  const file = question.interaction?.source_filename || "Unknown source";
  const page = question.interaction?.source_page;
  return page ? `${file} · page ${page}` : file;
}
function BankQuestionEditor({bankId,question,done,cancel,onDirtyChange,onFigureActive}:{bankId:string;question:Question;done:()=>Promise<void>;cancel:()=>void;onDirtyChange?:(dirty:boolean)=>void;onFigureActive?:(active:boolean)=>void}) {
 return <AdminQuestionEditor bankId={bankId} question={question} api={api} done={done} cancel={cancel} onDirtyChange={onDirtyChange} onFigureActive={onFigureActive} renderQuestion={(value)=><QuestionCard q={value} mode="teacher"/>}/>;
}

function optionMarkdown(option: any) {
  if (typeof option === "string") return option;
  if (option && typeof option === "object")
    return "markdown" in option ? (option.markdown ?? "") : (option.text || option.value || "");
  return String(option || "");
}
function needsFigureAdjustment(question: Question) {
  if ((question.diagrams || []).length) return true;
  return (question.interaction?.ingestion_issues || []).some((issue: unknown) =>
    /^diagram_|^option_diagram_/.test(String(issue))
  );
}
function QuestionDiagram({ q, diagram, index }: {
  q: Question; diagram: NonNullable<Question["diagrams"]>[number]; index: number;
}) {
  const [broken, setBroken] = useState(false);
  const src = `/api/media/questions/${q.id}/${index}${diagram.object_key ? `?asset=${encodeURIComponent(diagram.object_key)}` : ""}`;
  useEffect(() => setBroken(false), [src]);
  return broken ? (
    <div className="diagram-error">Diagram unavailable: {diagram.alt || "Source figure"}</div>
  ) : (
    <img className="diagram" src={src} alt={diagram.alt || "Source figure"} onError={() => setBroken(true)} />
  );
}
function OptionContent({ q, index }: { q: Question; index: number }) {
  return <div className="option-content">
    <Markdown value={optionMarkdown(q.options[index])} />
    {(q.diagrams || []).map((diagram, i) => diagram.role === "option" && diagram.option_index === index
      ? <QuestionDiagram key={i} q={q} diagram={diagram} index={i} /> : null)}
  </div>;
}
function QuestionCard({
  q,
  mode = "teacher",
  studentAnswer,
  displayNumber,
}: {
  q: Question;
  mode?: "teacher" | "student-result" | "exam";
  studentAnswer?: any;
  displayNumber?: number;
}) {
  const [showSolution, setShowSolution] = useState(false),
    correct = answerOptionIndex(q),
    correctOptions = new Set<number>(
      Array.isArray(q.answer?.options)
        ? q.answer.options
        : correct >= 0
          ? [correct]
          : [],
    ),
    showAnswer =
      mode !== "exam" &&
      (correctOptions.size > 0 ||
        q.answer?.value !== undefined ||
        q.answer?.matches),
    selectedOptions = Array.isArray(studentAnswer?.options)
      ? studentAnswer.options
      : studentAnswer?.option === undefined
        ? []
        : [studentAnswer.option],
    answerSummary = q.answer?.options?.length
      ? `options ${q.answer.options.map((x: number) => String.fromCharCode(65 + x)).join(", ")}`
      : correct >= 0
        ? `option ${String.fromCharCode(65 + correct)}`
        : q.answer?.value !== undefined
          ? String(q.answer.value)
          : q.answer?.matches
            ? "matrix matches"
            : "",
    questionDiagrams = (q.diagrams || [])
      .map((d, i) => ({ d, i }))
      .filter(({ d }) => d.role !== "solution" && d.role !== "option"),
    solutionDiagrams = (q.diagrams || [])
      .map((d, i) => ({ d, i }))
      .filter(({ d }) => d.role === "solution"),
    renderDiagram = ({ d, i }: { d: any; i: number }) =>
      <QuestionDiagram key={i} q={q} diagram={d} index={i} />;
  return (
    <article className="question">
      <b>
        {questionNumber(displayNumber === undefined ? q.source_number : String(displayNumber))} · {q.kind.replaceAll("_", " ")}
      </b>
      <Markdown value={mode === "teacher" ? q.stem_markdown : q.stem_markdown.replace(/^\s*(?:q(?:uestion)?\s*)?\d+\s*[.)]\s*/i, "")} />
      {questionDiagrams.map(renderDiagram)}
      {mode !== "exam" &&
        q.options?.map((o: any, i: number) => {
          const isCorrect = showAnswer && correctOptions.has(i),
            isIncorrectStudentAnswer =
              mode === "student-result" &&
              selectedOptions.includes(i) &&
              !isCorrect;
          return (
            <div
              key={i}
              className={`option ${isCorrect ? "correct-option" : ""} ${isIncorrectStudentAnswer ? "incorrect-option" : ""}`}
            >
              <span className="option-letter">
                {String.fromCharCode(65 + i)}
              </span>
              <OptionContent q={q} index={i} />
              {isCorrect && (
                <span className="answer-badge">Correct answer: option {String.fromCharCode(65 + i)}</span>
              )}
              {isIncorrectStudentAnswer && (
                <span className="answer-badge incorrect-answer-badge">
                  Your answer
                </span>
              )}
            </div>
          );
        })}
      {showAnswer && (
        <small className="answer-summary">Answer key: {answerSummary}</small>
      )}
      {(q.solution_markdown || solutionDiagrams.length > 0) && mode !== "exam" && (
        <>
          <button
            type="button"
            className="solution-toggle secondary-action"
            onClick={() => setShowSolution((x) => !x)}
          >
            {showSolution ? "Hide solution" : "View solution"}
          </button>
          {showSolution && (
            <div className="solution">
              <strong>Worked solution</strong>
              {q.solution_markdown && <Markdown value={q.solution_markdown} />}
              {solutionDiagrams.map(renderDiagram)}
            </div>
          )}
        </>
      )}
      {q.confidence !== undefined && mode === "teacher" && (
        <small>Extraction confidence: {Math.round(q.confidence * 100)}%</small>
      )}
    </article>
  );
}
function ExamStartGate({ test, start, cancel }: { test: Test; start: () => Promise<void>; cancel: () => void }) {
  const [starting, setStarting] = useState(false), [error, setError] = useState("");
  const begin = async () => {
    setError("");
    setStarting(true);
    try {
      if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
      await start();
    } catch {
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
      setError("We couldn't start the secure attempt. Allow fullscreen, check your connection, and try again.");
    } finally {
      setStarting(false);
    }
  };
  return <main className="exam-intro"><div className="exam-intro-card"><p className="eyebrow">READY TO BEGIN</p><h1>Review before your timed attempt starts</h1><p><b>{test.title}</b> · {test.question_count} questions · {Math.ceil(test.duration_seconds / 60)} minutes</p><ul><li>Your timer begins only after you continue below.</li><li>Fullscreen keeps the assessment focused and enables integrity monitoring.</li><li>Leaving fullscreen, changing apps, or hiding the page may flag or cancel the attempt.</li></ul>{error && <Notice kind="error">{error}</Notice>}<div className="exam-intro-actions"><button className="secondary-action" onClick={cancel} disabled={starting}>Back to assessments</button><button onClick={() => void begin()} disabled={starting}>{starting ? "Starting secure attempt…" : "Start secure attempt"}</button></div></div></main>;
}

function Exam({
  attempt,
  index,
  setIndex,
  update,
  finish,
  cancelForIntegrity,
  returnHome,
}: {
  attempt: any;
  index: number;
  setIndex: (n: number) => void;
  update: (q: string, a: any, m: boolean) => Promise<boolean>;
  finish: () => Promise<boolean>;
  cancelForIntegrity: (eventType: IntegrityEventType, clientTime: string) => Promise<any>;
  returnHome: () => Promise<void>;
}) {
  const blueprint = attempt.delivery_blueprint || { instructions: "Read every question carefully. Use Save & Next to record an answer.", sections: [{ id: "general", title: "General", question_ids: attempt.questions.map((question: any) => question.id) }] },
    [started, setStarted] = useState(() => Boolean(document.fullscreenElement)),
    [paletteOpen, setPaletteOpen] = useState(false),
    [submitReview, setSubmitReview] = useState(false),
    [draft, setDraft] = useState<any>(null),
    [draftReview, setDraftReview] = useState(false),
    [saving, setSaving] = useState(false),
    [saveError, setSaveError] = useState(""),
    [startError, setStartError] = useState(""),
    [integrityIncident, setIntegrityIncident] = useState<{ eventType: IntegrityEventType; clientTime: string; saved: boolean; cancelled?: boolean; error?: string } | null>(null),
    q = attempt.questions[index],
    r = attempt.responses[q.id] || {},
    [now, setNow] = useState(Date.now()),
    [expiryRetry, setExpiryRetry] = useState(0),
    expirySubmissionStarted = useRef(false),
    integrityTriggered = useRef(false),
    fullscreenEngaged = useRef(false),
    fullscreenRequestPending = useRef(false),
    mounted = useRef(true),
    focusRef = useRef<{questionId:string;started:number;token:string}|null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const x = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(x);
  }, []);
  const left = Math.max(0, new Date(attempt.ends_at).getTime() - now);
  useEffect(() => { setDraft(r.answer ?? null); setDraftReview(!!r.marked_for_review); setSaveError(""); }, [q.id, r.answer, r.marked_for_review]);
  useEffect(() => { if (started) void api(`/attempts/${attempt.id}/questions/${q.id}/visit`, { method: "POST" }); }, [attempt.id, q.id, started]);
  const flushFocus=()=>{
    const interval=focusRef.current;
    if(!interval)return Promise.resolve();
    focusRef.current=null;
    const duration=Math.min(900000,Math.max(0,Math.round(performance.now()-interval.started)));
    return api(`/attempts/${attempt.id}/questions/${interval.questionId}/focus-interval`,{method:"POST",body:JSON.stringify({interval_id:interval.token,duration_ms:duration})}).catch(()=>{});
  };
  useEffect(()=>{
    if(!started)return;
    focusRef.current={questionId:q.id,started:performance.now(),token:crypto.randomUUID()};
    return ()=>{void flushFocus()};
  },[attempt.id,q.id,started]);
  useEffect(() => {
    const closeOnEscape=(event:KeyboardEvent) => { if (event.key === "Escape") { setPaletteOpen(false); setSubmitReview(false); } };
    window.addEventListener("keydown",closeOnEscape);
    return () => window.removeEventListener("keydown",closeOnEscape);
  }, []);
  useEffect(() => {
    const storageKey = `testnow:integrity:${attempt.id}`;
    const clearPending = () => { try { window.localStorage.removeItem(storageKey); } catch {} };
    const report = (eventType: IntegrityEventType, clientTime = new Date().toISOString()) => {
      if (integrityTriggered.current || fullscreenRequestPending.current) return;
      integrityTriggered.current = true;
      try { window.localStorage.setItem(storageKey, JSON.stringify({ eventType, clientTime })); } catch {}
      setIntegrityIncident({ eventType, clientTime, saved: false });
      void cancelForIntegrity(eventType, clientTime)
        .then((result) => { clearPending(); if (mounted.current) setIntegrityIncident({ eventType, clientTime, saved: true, cancelled: Boolean(result.cancelled_at) }); })
        .catch((error: Error) => mounted.current && setIntegrityIncident({ eventType, clientTime, saved: false, error: error.message }));
    };
    try {
      const pending = JSON.parse(window.localStorage.getItem(storageKey) || "null");
      if (pending?.eventType && pending?.clientTime) report(pending.eventType as IntegrityEventType,pending.clientTime);
    } catch {}
    const onVisibility = () => { if (document.visibilityState === "hidden") report("tab_hidden"); };
    const onBlur = () => { if (!fullscreenRequestPending.current) report("window_blur"); };
    const onFullscreen = () => { if (fullscreenEngaged.current && !document.fullscreenElement) report("fullscreen_exit"); };
    const onPageHide = () => report("page_hidden");
    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("fullscreenchange", onFullscreen);
    window.addEventListener("blur", onBlur);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("fullscreenchange", onFullscreen);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [attempt.id, cancelForIntegrity]);
  useEffect(() => {
    if (!left && !attempt.submitted_at && !expirySubmissionStarted.current) {
      expirySubmissionStarted.current = true;
      integrityTriggered.current = true;
      void finish().then((submitted) => {
        if (!submitted && mounted.current) {
          integrityTriggered.current = false;
          expirySubmissionStarted.current = false;
          window.setTimeout(() => mounted.current && setExpiryRetry((value) => value + 1), 5000);
        }
      });
    }
  }, [left, attempt.submitted_at, expiryRetry, finish]);
  const sections = blueprint.sections as { id: string; title: string; question_ids: string[] }[];
  const activeSection = sections.find((section) => section.question_ids.includes(q.id)) || sections[0];
  const sectionId = activeSection?.id;
  const sectionQuestions = activeSection?.question_ids.map((id) => attempt.questions.find((question: any) => question.id === id)).filter(Boolean) || [];
  const draftChanged = JSON.stringify(draft) !== JSON.stringify(r.answer ?? null) || draftReview !== !!r.marked_for_review;
  const status = (question: any) => { const response = attempt.responses[question.id]; if (!response?.visited_at && !response?.answer && !response?.marked_for_review) return "unvisited"; if (response?.answer && response?.marked_for_review) return "answered-review"; if (response?.answer) return "answered"; if (response?.marked_for_review) return "review"; return "unanswered"; };
  const goto = (next: number) => { if (draftChanged) { setSaveError("Save or clear this response before moving to another question."); return; } setIndex(next); };
  const startExam = async () => {
    setStartError("");
    if (document.documentElement.requestFullscreen) {
      fullscreenRequestPending.current = true;
      try {
        await document.documentElement.requestFullscreen();
        fullscreenEngaged.current = Boolean(document.fullscreenElement);
      } catch {
        setStartError("Fullscreen access is required on this device. Allow fullscreen and try again.");
        return;
      } finally {
        fullscreenRequestPending.current = false;
      }
    }
    setStarted(true);
  };
  const finishNormally = async () => {
    integrityTriggered.current = true;
    await flushFocus();
    if (draftChanged) {
      setSaving(true);
      setSaveError("");
      const saved = await update(q.id, draft, draftReview);
      if (mounted.current) setSaving(false);
      if (!saved) {
        integrityTriggered.current = false;
        if (mounted.current) setSaveError("Could not save the current response. Check your connection and submit again.");
        return false;
      }
    }
    const submitted = await finish();
    if (!submitted) integrityTriggered.current = false;
    return submitted;
  };
  const save = async (review = draftReview, advance = false) => { setSaving(true); setSaveError(""); const ok = await update(q.id, draft, review); setSaving(false); if (!ok) { setSaveError("Could not save. Check your connection and try again."); return; } if (advance) { const pos = attempt.questions.findIndex((question: any) => question.id === q.id); setIndex(Math.min(pos + 1, attempt.questions.length - 1)); } };
  const multiple = q.kind === "multiple_choice";
  const matrix = q.kind === "matrix_match";
  const written = ["numerical", "integer", "subjective"].includes(q.kind);
  const toggle = (i: number) => {
    const selected: Array<number> = Array.isArray(draft?.options)
      ? draft.options
      : [];
    const next = selected.includes(i)
      ? selected.filter((x) => x !== i)
      : [...selected, i].sort((a, b) => a - b);
    setDraft({ options: next });
  };
  const toggleMatch = (row: string, choice: string) => {
    const matches: Record<string, string[]> = { ...(draft?.matches || {}) };
    const selected = matches[row] || [];
    matches[row] = selected.includes(choice)
      ? selected.filter((x) => x !== choice)
      : [...selected, choice].sort();
    setDraft({ matches });
  };
  const selectSingle = async (i: number) => {
    if (saving) return;
    const answer = { option: i };
    setDraft(answer);
    setSaving(true);
    setSaveError("");
    const ok = await update(q.id, answer, r.marked_for_review);
    if (mounted.current) {
      setSaving(false);
      if (!ok) setSaveError("Could not save this option. Check your connection and try again.");
    }
  };
  if (!started) return <main className="exam-intro"><div className="exam-intro-card"><p className="eyebrow">SECURE CBT</p><h1>Continue your secure attempt</h1><p>{blueprint.instructions}</p><ul><li>Your timer is running because this is a resumed attempt.</li><li>Keep this exam visible and in fullscreen until you submit.</li><li>Changing tabs, switching apps, hiding the page, or leaving fullscreen immediately cancels and flags the attempt.</li></ul>{startError && <Notice kind="error">{startError}</Notice>}<button onClick={startExam}>Continue in secure fullscreen</button></div></main>;
  return (
    <div className="exam">
      <header>
        <b>TestNow Exam Console</b>
        <strong>
          {Math.floor(left / 60000)}:
          {String(Math.floor(left / 1000) % 60).padStart(2, "0")}
        </strong>
        <span className={`save-state ${saving ? "saving" : saveError ? "error" : ""}`} aria-live="polite">{saving ? "Saving…" : saveError || (draftChanged ? "Unsaved changes" : "All changes saved")}</span>
        <button className="palette-trigger" onClick={() => setPaletteOpen(!paletteOpen)} aria-expanded={paletteOpen} aria-controls="question-palette">Questions</button>
      </header>
      <nav className="exam-sections" aria-label="Test sections" role="tablist">
        {sections.map((section) => <button key={section.id} role="tab" aria-selected={section.id === sectionId} className={section.id === sectionId ? "active" : ""} onClick={() => { const next = attempt.questions.findIndex((question: any) => question.id === section.question_ids[0]); if (next >= 0) goto(next); }}>{section.title}</button>)}
      </nav>
      <aside id="question-palette" className={paletteOpen ? "open" : ""}>
        <div className="palette-heading"><b>Question palette</b><button onClick={() => setPaletteOpen(false)} aria-label="Close question palette">×</button></div>
        <div className="palette-legend" aria-label="Question status legend"><span className="unvisited">Unvisited</span><span className="unanswered">Not answered</span><span className="answered">Answered</span><span className="review">Review</span><span className="answered-review">Answered & review</span></div>
        {sectionQuestions.map((x: any) => {
          const i = attempt.questions.findIndex((question: any) => question.id === x.id);
          return (
          <button
            className={`${i === index ? "active" : ""} ${status(x)}`}
            key={x.id}
            onClick={() => { goto(i); setPaletteOpen(false); }}
            aria-label={`Question ${i + 1}, ${status(x).replace("-", " ")}`}
          >
            {i + 1}
          </button>
        )})}
      </aside>
      <section>
        <p>
          QUESTION {index + 1} OF {attempt.questions.length}
        </p>
        <div className="exam-scroll">
          <QuestionCard q={q} mode="exam" displayNumber={index + 1} />
          <div className="answers">
            {written ? (
              <textarea
                aria-label="Your answer"
                value={draft?.value ?? ""}
                inputMode={q.kind === "integer" ? "numeric" : "decimal"}
                onChange={(e) => setDraft({ value: e.target.value })}
                placeholder={
                  q.kind === "subjective"
                    ? "Type your answer"
                    : "Enter your answer"
                }
              />
            ) : matrix ? (
              <div className="matrix-response">
                <p>Select every matching column for each row.</p>
                {(q.interaction?.matrix_items || []).map((row: string) => (
                  <div className="matrix-response-row" key={row}>
                    <strong>{row}</strong>
                    <div>
                      {(q.interaction?.matrix_choices || []).map(
                        (choice: string) => (
                          <button
                            type="button"
                            key={choice}
                            className={
                              (draft?.matches?.[row] || []).includes(choice)
                                ? "chosen"
                                : ""
                            }
                            onClick={() => toggleMatch(row, choice)}
                          >
                            {choice}
                          </button>
                        ),
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              q.options.map((option: any, i: number) => {
                const selected = multiple
                  ? (draft?.options || []).includes(i)
                  : JSON.stringify(draft) === JSON.stringify({ option: i });
                return (
                  <button
                    key={i}
                    className={selected ? "chosen" : ""}
                    disabled={saving}
                    onClick={() =>
                      multiple
                        ? toggle(i)
                        : void selectSingle(i)
                    }
                  >
                    <span className="option-letter">
                      {String.fromCharCode(65 + i)}
                    </span>
                    <OptionContent q={q} index={i} />
                  </button>
                );
              })
            )}
          </div>
        </div>
        <footer>
          <button onClick={() => setDraft(null)} disabled={saving}>
            Clear response
          </button>
          <button onClick={() => save(!draftReview, true)} disabled={saving}>
            Mark for Review & Next
          </button>
          <button onClick={() => save(draftReview, true)} disabled={saving}>
            Save & next
          </button>
          <button className="danger" onClick={() => setSubmitReview(true)} disabled={saving}>
            Submit test
          </button>
        </footer>
      </section>
      {submitReview && <div className="exam-dialog" role="dialog" aria-modal="true" aria-labelledby="submit-title"><div><h2 id="submit-title">Review before submitting</h2>{sections.map((section) => { const items=section.question_ids.map((id) => attempt.questions.find((question: any) => question.id===id)).filter(Boolean); return <p key={section.id}><b>{section.title}:</b> {items.filter((item: any) => status(item)==="answered" || status(item)==="answered-review").length} answered, {items.filter((item: any) => status(item)==="review" || status(item)==="answered-review").length} marked for review, {items.filter((item: any) => status(item)==="unanswered" || status(item)==="unvisited").length} unanswered.</p>; })}{draftChanged && <p className="notice">Your current response will be saved before the test is submitted.</p>}<div className="dialog-actions"><button onClick={() => setSubmitReview(false)}>Continue test</button><button className="danger" onClick={finishNormally} disabled={saving}>Submit test</button></div></div></div>}
      {integrityIncident && <div className="exam-dialog integrity-dialog" role="alertdialog" aria-modal="true"><div><p className="eyebrow">INTEGRITY WARNING</p><h2>{integrityIncident.saved && integrityIncident.cancelled === false ? "Integrity incident flagged" : "Assessment cancelled"}</h2><p>The secure exam detected <b>{integrityIncident.eventType.replaceAll("_", " ")}</b>. {integrityIncident.saved && integrityIncident.cancelled === false ? "The server had already closed your attempt, so its completed status was preserved and the incident was flagged for your teacher." : "Your saved responses were submitted, this attempt was cancelled immediately, and your teacher can see the incident."}</p>{!integrityIncident.saved && !integrityIncident.error && <Notice>Recording the incident securely…</Notice>}{integrityIncident.error && <Notice kind="error">The exam remains locked. Reconnect before closing this page so the incident can be recorded: {integrityIncident.error}</Notice>}<div className="dialog-actions">{integrityIncident.error && <button onClick={() => { const {eventType,clientTime}=integrityIncident; setIntegrityIncident({ eventType, clientTime, saved:false }); void cancelForIntegrity(eventType,clientTime).then((result) => { try { window.localStorage.removeItem(`testnow:integrity:${attempt.id}`); } catch {} setIntegrityIncident({ eventType, clientTime, saved:true, cancelled:Boolean(result.cancelled_at) }); }).catch((error:Error) => setIntegrityIncident({ eventType, clientTime, saved:false, error:error.message })); }}>Retry secure report</button>}<button disabled={!integrityIncident.saved} onClick={async () => { if (document.fullscreenElement) await document.exitFullscreen().catch(() => {}); await returnHome(); }}>Return to dashboard</button></div></div></div>}
    </div>
  );
}
type DiagnosticQuestion = { id:string; number:number; stem_markdown?:string; test_id:string; attempt_id:string; student_id?:string; subject:string; chapter:string; topic:string; skill:string; outcome:string; timing?:{seconds:number;visits:number}; possible_misconception?:string };
type DiagnosticGroup = { label:string; chapter:string; topic:string; question_count:number; evidence_count:number; student_count:number; correct:number; incorrect:number; unanswered:number; finding:string; confidence:string; questions:{id:string;number:number;test_id:string;attempt_id:string;student_id?:string;outcome:string}[] };
type DiagnosticData = { questions?:DiagnosticQuestion[]; skills?:DiagnosticGroup[]; topics?:DiagnosticGroup[]; attempts?:{attempt_id:string;test_id:string;test_title?:string;submitted_at:string;questions:DiagnosticQuestion[]}[] };
const diagnosticKey=(group:DiagnosticGroup)=>`${group.chapter}:${group.topic}:${group.label}`;
function OutcomeBar({correct,incorrect,unanswered}:{correct:number;incorrect:number;unanswered:number}){
  const total=correct+incorrect+unanswered;
  return <div className="diagnostic-outcome-bar" role="img" aria-label={`${correct} correct, ${incorrect} incorrect, ${unanswered} unanswered`}>
    {total>0&&<><span className="is-correct" style={{width:`${correct/total*100}%`}}/><span className="is-incorrect" style={{width:`${incorrect/total*100}%`}}/><span className="is-unanswered" style={{width:`${unanswered/total*100}%`}}/></>}
  </div>
}
function TimingChart({questions,onSelect}:{questions:DiagnosticQuestion[];onSelect?:(q:DiagnosticQuestion)=>void}){
  const timed=[...questions].filter(q=>q.timing).sort((a,b)=>(b.timing?.seconds||0)-(a.timing?.seconds||0)).slice(0,7);
  if(!timed.length)return <p className="diagnostic-empty">Question focus time was not captured for this test.</p>;
  const max=Math.max(...timed.map(q=>q.timing?.seconds||0),1);
  return <div className="diagnostic-time-chart" aria-label="Questions with most focus time">{timed.map(q=><button type="button" key={`${q.attempt_id}:${q.id}`} onClick={()=>onSelect?.(q)} className="diagnostic-time-row"><span>Q{q.number}</span><span className="diagnostic-time-track"><i style={{width:`${Math.max(3,(q.timing?.seconds||0)/max*100)}%`}} className={q.outcome}/></span><b>{Math.round(q.timing?.seconds||0)}s</b></button>)}</div>
}
function DiagnosticSummary({data,title="Learning diagnostics"}:{data?:DiagnosticData;title?:string}) {
  const [selectedKey,setSelectedKey]=useState("");
  if (!data) return null;
  const questions=data.questions || data.attempts?.flatMap(item=>item.questions) || [];
  const skills=data.skills||[];
  const selected=skills.find(group=>diagnosticKey(group)===selectedKey)||skills[0];
  const relevant=selected?questions.filter(q=>q.skill===selected.label&&q.chapter===selected.chapter&&q.topic===selected.topic):questions;
  const counts={correct:questions.filter(q=>q.outcome==="correct").length,incorrect:questions.filter(q=>q.outcome==="incorrect").length,unanswered:questions.filter(q=>q.outcome==="unanswered").length};
  const weak=skills.filter(group=>group.incorrect+group.unanswered>group.correct).length;
  const selectQuestion=(question:DiagnosticQuestion)=>{const group=skills.find(item=>item.label===question.skill&&item.chapter===question.chapter&&item.topic===question.topic);if(group)setSelectedKey(diagnosticKey(group))};
  return <Card className="diagnostic-panel diagnostic-dashboard">
    <div className="diagnostic-heading"><div><p className="eyebrow">LEARNING DIAGNOSTICS</p><h2>{title}</h2><p>Explore the skills behind your result, then inspect the exact questions.</p></div><span className="diagnostic-sample">{questions.length} questions analysed</span></div>
    <div className="diagnostic-overview"><div><strong>{counts.correct}</strong><span>Correct</span></div><div><strong>{counts.incorrect}</strong><span>Incorrect</span></div><div><strong>{counts.unanswered}</strong><span>Unanswered</span></div><div><strong>{weak}</strong><span>Skills to revisit</span></div></div>
    <OutcomeBar {...counts}/><div className="diagnostic-legend"><span><i className="is-correct"/>Correct</span><span><i className="is-incorrect"/>Incorrect</span><span><i className="is-unanswered"/>Unanswered</span></div>
    {!!data.attempts?.length&&<div className="diagnostic-history"><b>Test history</b>{data.attempts.map(item=><span key={item.attempt_id}>{item.test_title||"Assessment"} · {fmtDate(item.submitted_at)} · {item.questions.filter(q=>q.outcome==="correct").length}/{item.questions.length} correct</span>)}</div>}
    <div className="diagnostic-viz-grid"><section><div className="diagnostic-section-title"><h3>Skill breakdown</h3><small>Select a skill to see its evidence</small></div><div className="diagnostic-skills-chart">{skills.length?skills.map(group=><button type="button" key={diagnosticKey(group)} aria-pressed={diagnosticKey(group)===diagnosticKey(selected)} className={diagnosticKey(group)===diagnosticKey(selected)?"selected":""} onClick={()=>setSelectedKey(diagnosticKey(group))}><span><b>{group.label}</b><small>{group.chapter} · {group.evidence_count} questions</small></span><span className="diagnostic-skill-score">{group.correct}/{group.evidence_count}</span><OutcomeBar correct={group.correct} incorrect={group.incorrect} unanswered={group.unanswered}/></button>):<p className="diagnostic-empty">Skill labels are being prepared for this test.</p>}</div></section>
    <section><div className="diagnostic-section-title"><h3>Where time went</h3><small>Top focused questions</small></div><TimingChart questions={questions} onSelect={selectQuestion}/><div className="diagnostic-time-legend">Bar colour shows the answer outcome. Time alone does not diagnose a gap.</div></section></div>
    <section className="diagnostic-evidence"><div className="diagnostic-section-title"><h3>{selected?selected.label:"Question evidence"}</h3><small>{selected?`${selected.finding} · ${selected.confidence} · ${selected.evidence_count} questions`:"Choose a skill above"}</small></div><div className="diagnostic-evidence-list">{relevant.map(q=><article key={`${q.attempt_id}:${q.id}`}><div className="diagnostic-evidence-head"><b>Question {q.number}</b><span className={`diagnostic-status ${q.outcome}`}>{q.outcome}</span><small>{q.timing?`${Math.round(q.timing.seconds)}s focused · ${q.timing.visits} visit${q.timing.visits===1?"":"s"}`:"Time unavailable"}</small></div>{q.stem_markdown&&<Markdown value={q.stem_markdown} className="diagnostic-question-math"/>}{q.possible_misconception&&<p>Possible misconception: {q.possible_misconception}</p>}{data.questions&&<a href={`#result-question-${q.number}`}>Review answer and solution →</a>}</article>)}</div></section>
    <p className="diagnostic-footnote">Skill findings are based on these answers, with limited evidence labelled. Missing timing is shown as unavailable.</p>
  </Card>
}
function StudentProgress(){
  const [data,setData]=useState<DiagnosticData|null>(null);
  useEffect(()=>{api("/student/diagnostics").then(setData).catch(()=>setData(null))},[]);
  if(!data?.attempts?.length)return null;
  return <DiagnosticSummary data={data} title="Progress across tests"/>;
}
function TeacherDiagnostics({testId}:{testId:string}){
  const [data,setData]=useState<any>(null),[classId,setClassId]=useState(""),[student,setStudent]=useState<any>(null),[error,setError]=useState(""),[selectedKey,setSelectedKey]=useState("");
  useEffect(()=>{setData(null);setStudent(null);api(`/tests/${testId}/diagnostics${classId?`?class_id=${encodeURIComponent(classId)}`:""}`).then(setData).catch((e:Error)=>setError(e.message))},[testId,classId]);
  const openStudent=(id:string)=>{setError("");api(`/teacher/students/${id}/diagnostics`).then(setStudent).catch((e:Error)=>setError(e.message))};
  const skills:DiagnosticGroup[]=data?.skills||[];
  const selected=skills.find(group=>diagnosticKey(group)===selectedKey)||skills[0];
  const studentRows:any[]=data?.students||[];
  const completed=data?.student_count||0;
  return <Card className="diagnostic-panel diagnostic-dashboard teacher-diagnostic-dashboard"><div className="diagnostic-heading"><div><p className="eyebrow">BATCH DIAGNOSTICS</p><h2>Where this batch needs attention</h2><p>Compare skills, then open a student to see their questions and progress.</p></div><label>Batch <select value={classId} onChange={e=>setClassId(e.target.value)}><option value="">All assigned students</option>{data?.batches?.map((batch:any)=><option key={batch.id} value={batch.id}>{batch.name}</option>)}</select></label></div>
    {error&&<Notice kind="error">{error}</Notice>}{!data?<p>Loading diagnostics…</p>:<><div className="diagnostic-batch-kpis"><div><strong>{completed}</strong><span>Completed students</span></div><div><strong>{skills.length}</strong><span>Skills assessed</span></div><div><strong>{skills.filter(group=>group.incorrect+group.unanswered>group.correct).length}</strong><span>Skills needing review</span></div></div>
    <div className="diagnostic-viz-grid"><section><div className="diagnostic-section-title"><h3>Skill distribution</h3><small>Correct / incorrect / unanswered responses</small></div><div className="diagnostic-skills-chart">{skills.map(group=><button type="button" key={diagnosticKey(group)} className={selected&&diagnosticKey(group)===diagnosticKey(selected)?"selected":""} aria-pressed={selected&&diagnosticKey(group)===diagnosticKey(selected)} onClick={()=>setSelectedKey(diagnosticKey(group))}><span><b>{group.label}</b><small>{group.student_count} {group.student_count===1?"student":"students"} · {group.question_count} {group.question_count===1?"question":"questions"}</small></span><span className="diagnostic-skill-score">{group.correct}/{group.evidence_count}</span><OutcomeBar correct={group.correct} incorrect={group.incorrect} unanswered={group.unanswered}/></button>)}</div></section>
    <section><div className="diagnostic-section-title"><h3>Students in selected skill</h3><small>Select a student for their finer picture</small></div><div className="diagnostic-heatmap">{studentRows.length?studentRows.map(row=>{const skill=row.skills.find((item:DiagnosticGroup)=>selected&&diagnosticKey(item)===diagnosticKey(selected));return <button type="button" key={row.student.id} onClick={()=>openStudent(row.student.id)}><b>{row.student.name}</b><span>{selected?.label||"Skill"}</span>{skill?<><OutcomeBar correct={skill.correct} incorrect={skill.incorrect} unanswered={skill.unanswered}/><strong>{skill.correct}/{skill.evidence_count} correct</strong></>:<small>No questions in this skill</small>}</button>}):<p className="diagnostic-empty">No submitted student attempts yet.</p>}</div></section></div>
    {selected&&<div className="diagnostic-batch-evidence"><h3>{selected.label} · question coverage</h3><p>{selected.student_count} {selected.student_count===1?"student":"students"} · {selected.question_count} distinct {selected.question_count===1?"question":"questions"} · {selected.incorrect} incorrect {selected.incorrect===1?"response":"responses"}. Select a student above to inspect their answers.</p><div className="diagnostic-question-chips">{[...new Set(selected.questions.map(q=>q.number))].sort((a,b)=>a-b).map(number=><span key={number}>Q{number}</span>)}</div></div>}</>}
    {student&&<div className="diagnostic-student-detail"><button className="secondary-action" onClick={()=>setStudent(null)}>Close student detail</button><DiagnosticSummary data={student} title={`${student.student.name} · progress across tests`}/></div>}
  </Card>
}
function Result({ attempt }: { attempt: any }) {
  const [data, setData] = useState<any>();
  const [explainers, setExplainers] = useState<Record<string, { url: string }> >({});
  useEffect(() => {
    api(`/results/${attempt.id}`)
      .then(setData)
      .catch((e) => setData({ error: e.message }));
    api(`/results/${attempt.id}/explainers`).then(setExplainers).catch(() => setExplainers({}));
  }, [attempt.id]);
  if (!data) return <div className="page">Loading result…</div>;
  if (data.error)
    return (
      <div className="page">
        <h1>Result pending</h1>
        <p>{data.error}</p>
      </div>
    );
  return (
    <div className="page">
      <h1>Score: {data.attempt.score}</h1>
      <Card><b>Total time taken</b><p>{fmtDuration(data.attempt.duration_seconds)}</p></Card>
      <DiagnosticSummary data={data.diagnostics} />
      {data.questions.map(
        (q: Question & { response: any; correct: boolean; points: number; }, index: number) => (
          <section key={q.id} id={`result-question-${index+1}`}><Card>
            <QuestionCard
              q={q}
              mode="student-result"
              studentAnswer={q.response}
              displayNumber={index + 1}
            />
            {explainers[q.id] && <div className="result-explainer"><h3>Visual explainer</h3><video controls preload="metadata" src={explainers[q.id].url} aria-label={`Video explainer for question ${index + 1}`} /></div>}
            <p>
              Your response:{" "}
              {q.response?.option !== undefined
                ? `Option ${String.fromCharCode(65 + q.response.option)}`
                : "No response"}{" "}
              · {q.response==null ? "Unanswered" : q.correct ? "Correct" : "Incorrect"} · {q.points} marks
            </p>
          </Card></section>
        ),
      )}
    </div>
  );
}

function AdminBankImporter({ notify }: { notify: (message: string) => void }) {
  const [banks, setBanks] = useState<Bank[]>([]),
    [detail, setDetail] = useState<Bank | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => setBanks(await api("/admin/question-banks"));
  const open = async (id: string) =>
    setDetail(await api(`/admin/question-banks/${id}`));
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  const create = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      const bank: Bank = await api("/admin/question-banks", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(e.currentTarget))),
      });
      await load();
      await open(bank.id);
      e.currentTarget.reset();
      notify("Draft bank created.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const upload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!detail) return;
    setBusy(true);
    try {
      const result = await fetch(
        `/api/admin/question-banks/${detail.id}/imports`,
        {
          method: "POST",
          credentials: "include",
          body: new FormData(e.currentTarget),
        },
      );
      if (!result.ok)
        throw new Error(
          (await result.json().catch(() => ({ detail: "Upload failed" })))
            .detail || "Upload failed",
        );
      await open(detail.id);
      await load();
      notify("Sources queued for extraction.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const publish = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const result = await publishWithPreview(`/admin/question-banks/${detail.id}`, detail.name);
      if (!result) return;
      await open(detail.id);
      await load();
      notify(publicationMessage(result));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page">
      <p className="eyebrow">SUPER ADMIN · QUESTION BANKS</p>
      <h1>Create and publish question banks</h1>
      {error && <Notice kind="error">{error}</Notice>}
      <div className="class-layout">
        <aside className="class-list">
          <Card>
            <h2>Create bank</h2>
            <form className="stacked-form" onSubmit={create}>
              <label>
                Name
                <input name="name" required />
              </label>
              <label>
                Exam code <span className="optional">(optional)</span>
                <input name="exam_code" placeholder="JEE" />
              </label>
              <button disabled={busy}>Create draft</button>
            </form>
          </Card>
          <Card>
            <h2>Banks</h2>
            {banks.map((bank) => (
              <button
                className={`class-choice ${detail?.id === bank.id ? "selected" : ""}`}
                key={bank.id}
                onClick={() => open(bank.id)}
              >
                <b>{bank.name}</b>
                <small>
                  {bank.status} · {bank.question_count} questions ·{" "}
                  {bank.flagged_count} flagged
                </small>
              </button>
            ))}
          </Card>
        </aside>
        <section>
          {detail ? (
            <>
              <Card>
                <div className="section-heading">
                  <div>
                    <h2>{detail.name}</h2>
                    <p>
                      {detail.exam_code} · {detail.status}
                    </p>
                  </div>
                  <button
                    onClick={publish}
                    disabled={busy || detail.status === "published"}
                  >
                    Publish complete bank
                  </button>
                </div>
                <p>
                  Teachers see the bank only after all flags are resolved and it
                  is published.
                </p>
              </Card>
              {detail.status !== "published" && (
                <Card>
                  <h2>Upload source documents</h2>
                  <form onSubmit={upload} className="stacked-form">
                    <label>
                      Subject
                      <input name="subject" placeholder="Physics" required />
                    </label>
                    <label>
                      Question documents
                      <input
                        name="question_files"
                        type="file"
                        accept=".pdf,.docx"
                        multiple
                        required
                      />
                    </label>
                    <label>
                      Answer / solution documents{" "}
                      <span className="optional">(optional)</span>
                      <input
                        name="answer_files"
                        type="file"
                        accept=".pdf,.docx"
                        multiple
                      />
                    </label>
                    <small>
                      Use matching filenames for pairs. Upload a combined paper
                      as a question document.
                    </small>
                    <button disabled={busy}>
                      {busy ? "Uploading…" : "Upload and start extraction"}
                    </button>
                  </form>
                </Card>
              )}
              <Card>
                <h2>Import status</h2>
                {detail.imports?.length ? (
                  detail.imports.map((job) => (
                    <article className="row" key={job.id}>
                      <div>
                        <b>{job.filename}</b>
                        <small>
                          {job.meta?.phase} · {job.meta?.question_count ?? 0}{" "}
                          questions · {job.meta?.flagged_count ?? 0} flagged
                        </small>
                      </div>
                      <span className={`badge ${job.status}`}>
                        {job.status}
                      </span>
                    </article>
                  ))
                ) : (
                  <p>No sources uploaded yet.</p>
                )}
              </Card>
              <Card>
                <h2>Review queue</h2>
                {detail.questions
                  ?.filter(
                    (question) =>
                      (question.interaction?.ingestion_issues || []).length,
                  )
                  .map((question) => (
                    <AdminBankFlag
                      key={question.id}
                      bankId={detail.id}
                      question={question}
                      done={() => open(detail.id)}
                    />
                  )) || (
                  <p>
                    Clean and resolved records will appear here after
                    extraction.
                  </p>
                )}
                <small>
                  Questions with missing answers must be re-imported with a
                  matched answer source.
                </small>
              </Card>
            </>
          ) : (
            <Card>Create or select a bank to begin.</Card>
          )}
        </section>
      </div>
    </div>
  );
}
function AdminBankFlag({
  bankId,
  question,
  done,
}: {
  bankId: string;
  question: Question;
  done: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const resolve = async () => {
    setBusy(true);
    try {
      await api(
        `/admin/question-banks/${bankId}/questions/${question.id}/resolve`,
        {
          method: "POST",
          body: JSON.stringify({
            source_number: question.source_number,
            kind: question.kind,
            stem_markdown: question.stem_markdown,
            options: question.options,
            answer: question.answer,
            solution_markdown: question.solution_markdown,
            scoring: question.scoring || {
              correct: 4,
              incorrect: -1,
              unanswered: 0,
            },
            diagrams: question.diagrams || [],
            source_spans: [],
            interaction: question.interaction || {},
            confidence: question.confidence ?? 0,
          }),
        },
      );
      await done();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="review-question">
      <QuestionCard q={question} mode="teacher" />
      <Notice kind="error">
        Needs review:{" "}
        {(question.interaction?.ingestion_issues || []).join(", ")}
      </Notice>
      {question.answer ? (
        <button onClick={resolve} disabled={busy}>
          {busy ? "Saving…" : "Approve reviewed question"}
        </button>
      ) : (
        <small>
          Add an answer via a matched answer/solution source, then re-import.
        </small>
      )}
      {error && <Notice kind="error">{error}</Notice>}
    </article>
  );
}
