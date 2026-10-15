"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { FigureEditor } from "./figure-editor";

type User = {
  id: string;
  name: string;
  email: string;
  role: "teacher" | "student" | "admin";
  status?: "active" | "suspended" | "pending_code";
  last_login_at?: string;
};
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
type Question = {
  id: string;
  source_number?: string;
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
};
type ImportJob = {
  id: string;
  status: "uploaded" | "processing" | "review" | "failed" | "approved";
  filename?: string;
  source_id?: string;
  error?: string;
  errors?: { page?: number; end_page?: number; message: string }[];
  question_count?: number;
  approved_count?: number;
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
    error_log?: { page?: number; message: string }[];
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
  availability?: "available" | "upcoming" | "closed";
  attempts_remaining?: number;
  latest_attempt?: {
    id: string;
    status: "submitted" | "in_progress" | "expired";
    started_at?: string;
    submitted_at?: string;
    score?: number;
  } | null;
};
type Analytics = {
  summary: {
    assigned: number;
    started: number;
    submitted: number;
    pending: number;
  };
  students: {
    student: User;
    status: string;
    score?: number;
    started_at?: string;
    submitted_at?: string;
    attempt_count: number;
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
  imports?: ImportJob[];
  questions?: Question[];
};

const errorMessage = (detail: unknown) =>
  Array.isArray(detail)
    ? detail.map((item: any) => item.msg || "Invalid request").join(" ")
    : typeof detail === "string"
      ? detail
      : "Request failed";
const api = async (path: string, options: RequestInit = {}) => {
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
    throw new Error(errorMessage(body.detail));
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
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {value}
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
  return <p className={`notice ${kind}`}>{children}</p>;
}

export function Portal() {
  const [user, setUser] = useState<User | null>(null),
    [error, setError] = useState(""),
    [view, setView] = useState<
      | "home"
      | "import"
      | "reviews"
      | "builder"
      | "classes"
      | "bank-import"
      | "test-detail"
      | "exam"
      | "result"
    >("home"),
    [tests, setTests] = useState<Test[]>([]),
    [questions, setQuestions] = useState<Question[]>([]),
    [classes, setClasses] = useState<Classroom[]>([]),
    [studentClasses, setStudentClasses] = useState<StudentClass[]>([]),
    [jobs, setJobs] = useState<ImportJob[]>([]),
    [admin, setAdmin] = useState<AdminDashboard | null>(null),
    [selectedJobId, setSelectedJobId] = useState<string | null>(null),
    [selectedTestId, setSelectedTestId] = useState<string | null>(null),
    [attempt, setAttempt] = useState<any>(null),
    [index, setIndex] = useState(0),
    [message, setMessage] = useState("");
  const previousJobs = useRef<Map<string, string>>(new Map()),
    sessionVersion = useRef(0);
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
      setAdmin(await api("/admin/dashboard"));
    } else if (me.role === "teacher") {
      const [loadedTests, loadedQuestions, loadedClasses] = await Promise.all([
        api("/tests"),
        api("/questions"),
        api("/classes"),
      ]);
      if (version !== sessionVersion.current) return;
      setTests(loadedTests);
      setQuestions(loadedQuestions);
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
    setError("");
    try {
      const x = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(e.currentTarget))),
      });
      await load(x);
    } catch (e: any) {
      setError(e.message);
    }
  };
  if (!user)
    return (
      <main className="auth">
        <div className="brand">
          <b>TestNow</b>
          <span>SECURE CBT DELIVERY</span>
          <h1>Assessment, delivered with confidence.</h1>
          <p>Choose your portal and use the access code issued to you.</p>
        </div>
        <Card>
          <h2>Sign in</h2>
          <form onSubmit={login} className="stacked-form">
            <div>
              <label>I am a</label>
              <select name="role" defaultValue="student">
                <option value="student">Student</option>
                <option value="teacher">Teacher</option>
              </select>
            </div>
            <div>
              <label>Email</label>
              <input name="email" type="email" autoComplete="email" required />
            </div>
            <div>
              <label>5-digit access code</label>
              <input
                name="access_code"
                inputMode="numeric"
                pattern="[0-9]{5}"
                maxLength={5}
                autoComplete="one-time-code"
                required
              />
            </div>
            <button>Continue</button>
          </form>
          {error && <Notice kind="error">{error}</Notice>}
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
  };
  const openTest = (id: string) => {
    setSelectedTestId(id);
    setView("test-detail");
  };
  return (
    <main>
      {view !== "exam" && (
        <header>
          <button className="logo" onClick={() => setView("home")}>
            Test<span>Now</span>
          </button>
          <nav>
            {user.role === "teacher" && (
              <>
                <button className={view === "home" ? "active" : ""} onClick={() => setView("home")}>Dashboard</button>
                <button className={view === "classes" ? "active" : ""} onClick={() => setView("classes")}>
                  Class / batch
                </button>
                <button className={view === "import" ? "active" : ""} onClick={() => setView("import")}>Import paper</button>
                <button className={view === "reviews" ? "active" : ""} onClick={() => setView("reviews")}>
                  Review papers
                </button>
                <button className={view === "builder" ? "active" : ""} onClick={() => setView("builder")}>Build test</button>
              </>
            )}
            {user.role === "admin" && (
              <>
                <button className={view === "home" ? "active" : ""} onClick={() => setView("home")}>Admin dashboard</button>
                <button className={view === "bank-import" ? "active" : ""} onClick={() => setView("bank-import")}>
                  Question banks
                </button>
              </>
            )}
            <button onClick={logout}>Sign out</button>
          </nav>
        </header>
      )}
      {message && <div className="toast">{message}</div>}
      {view === "home" &&
        (user.role === "student" ? (
          <StudentHome
            user={user}
            tests={tests}
            classes={studentClasses}
            start={async (id) => {
              try {
                setAttempt(
                  await api(`/tests/${id}/attempts`, { method: "POST" }),
                );
                setIndex(0);
                setView("exam");
              } catch (e: any) {
                setMessage(e.message);
              }
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
            questions={questions}
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
      {view === "builder" && (
        <Builder
          questions={questions}
          onDone={async (id) => {
            openTest(id);
            setMessage("Draft created. Add classes and publish it when ready.");
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
          finish={async () => {
            try {
              await api(`/attempts/${attempt.id}/submit`, { method: "POST" });
              setView("result");
            } catch (e: any) {
              setMessage(e.message);
            }
          }}
          update={async (qid, answer, marked) => {
            try {
              const x = await api(`/attempts/${attempt.id}/responses/${qid}`, {
                method: "PUT",
                body: JSON.stringify({
                  answer,
                  marked_for_review: marked,
                  revision: attempt.revision,
                }),
              });
              setAttempt({
                ...attempt,
                revision: x.revision,
                responses: {
                  ...attempt.responses,
                  [qid]: { answer, marked_for_review: marked },
                },
              });
            } catch (e: any) {
              setMessage(e.message);
            }
          }}
        />
      )}
      {view === "result" && attempt && <Result attempt={attempt} />}
    </main>
  );
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
function TeacherHome({
  tests,
  questions,
  classes,
  jobs,
  onTestDeleted,
  openImport,
  openTest,
  refresh,
}: {
  tests: Test[];
  questions: Question[];
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
  const deleteTest = async (test: Test) => {
    if (!window.confirm(`Delete “${test.title}”? This cannot be undone.`)) return;
    try {
      await api(`/tests/${test.id}`, { method: "DELETE" });
      onTestDeleted(test.id);
      // The delete is already confirmed. Refresh in the background so an
      // unrelated question-library failure cannot report it as unsuccessful.
      void Promise.resolve(refresh()).catch(() => {});
    } catch (e: any) {
      window.alert(e.message);
    }
  };
  return (
    <div className="page">
      <p className="eyebrow">TEACHER WORKSPACE</p>
      <h1>Assessment control centre</h1>
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
          <b>{questions.filter((q) => q.approved).length}</b>
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
            <label>Name</label>
            <input name="name" required />
          </div>
          <div>
            <label>Email</label>
            <input name="email" type="email" required />
          </div>
          <div>
            <label>5-digit access code</label>
            <input
              name="access_code"
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
                      <b>{teacher.name}</b>
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
                <label>Name</label>
                <input name="name" placeholder="e.g. Physics 12A" required />
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
                  <label>Name</label>
                  <input name="name" required />
                </div>
                <div>
                  <label>Email</label>
                  <input name="email" type="email" required />
                </div>
                <div>
                  <label>5-digit access code</label>
                  <input
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

function TestDetail({
  testId,
  allClasses,
  back,
  refresh,
  notify,
}: {
  testId: string;
  allClasses: Classroom[];
  back: () => void;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [test, setTest] = useState<Test | null>(null),
    [analytics, setAnalytics] = useState<Analytics | null>(null),
    [tab, setTab] = useState<"preview" | "assignment" | "analytics">("preview"),
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
    if (!window.confirm(`Delete “${test.title}”? This cannot be undone.`)) return;
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
      </nav>
      {tab === "preview" && (
        <Card>
          <h2>Immutable test preview</h2>
          <p>
            Students receive this question snapshot when they start the
            assessment.
          </p>
          {test.questions?.map((q) => (
            <QuestionCard key={q.id} q={q} mode="teacher" />
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
      {tab === "analytics" && <AnalyticsPanel analytics={analytics} />}
    </div>
  );
}
function AnalyticsPanel({ analytics }: { analytics: Analytics | null }) {
  if (!analytics) return <Card>Loading analytics…</Card>;
  return (
    <>
      <div className="stats analytics-stats">
        <Card>
          <b>{analytics.summary.assigned}</b>
          <span>Assigned</span>
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
          <span>Pending</span>
        </Card>
      </div>
      <Card>
        <h2>Student participation</h2>
        {analytics.students.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Status</th>
                  <th>Score</th>
                  <th>Started</th>
                  <th>Submitted</th>
                </tr>
              </thead>
              <tbody>
                {analytics.students.map((row) => (
                  <tr key={row.student.id}>
                    <td>
                      <b>{row.student.name}</b>
                      <small>{row.student.email}</small>
                    </td>
                    <td>
                      <span className={`badge ${row.status}`}>
                        {row.status.replace("_", " ")}
                      </span>
                    </td>
                    <td>{row.score ?? "—"}</td>
                    <td>{fmtDate(row.started_at)}</td>
                    <td>{fmtDate(row.submitted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
  onDeleted,
  refresh,
  reviewOnly = false,
}: {
  onDone: (x: ImportJob) => void;
  job: ImportJob | null;
  jobs: ImportJob[];
  selectJob: (id: string | null) => void;
  openReview?: (id: string) => void;
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
    if (!window.confirm(`Delete “${item.meta?.paper_title || item.filename || "this import"}”?`)) return;
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
                  </small>
                </button>
              ))
            ) : (
              <p>No papers uploaded yet.</p>
            )}
          </aside>
          <div className="review-main">
            {job && (
              <ImportReview job={job} active={!!active} refresh={refresh} />
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
}: {
  job: ImportJob;
  active: boolean;
  refresh: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [questionIndex, setQuestionIndex] = useState(0);
  useEffect(() => setQuestionIndex(0), [job.id]);
  const reviewQuestions = job.questions || [],
    currentQuestion = reviewQuestions[questionIndex];
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
          <div className="review-question-context">
            <span>Question {questionIndex + 1} of {reviewQuestions.length}</span>
            <span>{currentQuestion.kind.replaceAll("_", " ")}</span>
          </div>
          <ReviewQuestion
            key={currentQuestion.id}
            jobId={job.id}
            question={currentQuestion}
            onChanged={refresh}
          />
          <div className="review-pagination">
            <button type="button" className="secondary-action" disabled={!questionIndex} onClick={() => setQuestionIndex((index) => index - 1)}>Previous</button>
            <span>{questionIndex + 1} / {reviewQuestions.length}</span>
            <button type="button" className="secondary-action" disabled={questionIndex === reviewQuestions.length - 1} onClick={() => setQuestionIndex((index) => index + 1)}>Next question</button>
          </div>
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
}: {
  jobId: string;
  question: Question;
  onChanged: () => void;
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
  return (
    <Card className="review-question">
      <QuestionCard q={question} mode="teacher" />
      {needsFigureAdjustment(question) && <FigureEditor questionId={question.id} options={question.options} api={api} done={onChanged} />}
      {(question.interaction?.ingestion_issues || []).length > 0 && <Notice kind="error">
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
          </div>
          {error && <Notice kind="error">{error}</Notice>}
        </div>
      )}
    </Card>
  );
}

function Builder({
  questions,
  onDone,
}: {
  questions: Question[];
  onDone: (id: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]),
    [error, setError] = useState(""),
    [banks, setBanks] = useState<any[]>([]),
    [bankId, setBankId] = useState(""),
    [nodes, setNodes] = useState<any[]>([]),
    [source, setSource] = useState<"bank" | "mine">("bank"),
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
    [availability, setAvailability] = useState<{
      total: number;
      counts: Record<string, number>;
    }>({ total: 0, counts: {} }),
    [loading, setLoading] = useState(false),
    [countsLoading, setCountsLoading] = useState(false);
  const activeBank = banks.find((bank) => bank.id === bankId);
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
        : questions.filter((q) => q.approved && q.scope !== "global"),
    selectedQuestions = selected.map((id) => shown.find((question) => question.id === id)).filter(Boolean) as Question[],
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
        const result = await api("/tests", {
          method: "POST",
          body: JSON.stringify({
            ...f,
            opens_at: toIso(f.opens_at),
            closes_at: toIso(f.closes_at),
            duration_minutes: Number(f.duration_minutes),
            max_attempts: Number(f.max_attempts),
            marks_per_question: Number(f.marks_per_question),
            question_ids: selected,
            results_policy: { mode: "immediate" },
          }),
        });
        onDone(result.id);
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
          <div className="stacked-form">
            <div>
              <label>Test title</label>
              <input name="title" required />
            </div>
            <div>
              <label>Subject</label>
              <input name="subject" required />
            </div>
            <div>
              <label>Duration (minutes)</label>
              <input
                name="duration_minutes"
                type="number"
                min="1"
                defaultValue="60"
                required
              />
            </div>
            <div>
              <label>Maximum attempts</label>
              <input
                name="max_attempts"
                type="number"
                min="1"
                defaultValue="1"
                required
              />
            </div>
            <div>
              <label>Marks per question</label>
              <input name="marks_per_question" type="number" min="1" defaultValue="4" required />
            </div>
            <div>
              <label>
                Opens at <span className="optional">(optional)</span>
              </label>
              <input name="opens_at" type="datetime-local" />
            </div>
            <div>
              <label>
                Closes at <span className="optional">(optional)</span>
              </label>
              <input name="closes_at" type="datetime-local" />
            </div>
          </div>
        </Card>
        <Card className="builder-library">
          <h2>
            {source === "bank"
              ? `${activeBank?.name || "Global"} global question bank`
              : "Your approved questions"}
          </h2>
          <div className="stacked-form compact-form">
            <label>
              Question source
              <select
                value={source}
                onChange={(e) => {
                  setSource(e.target.value as "bank" | "mine");
                  setPage(1);
                }}
              >
                <option value="bank" disabled={!bankId}>
                  Global bank
                </option>
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
                    checked={selected.includes(q.id)}
                    onChange={() =>
                      setSelected((x) =>
                        x.includes(q.id)
                          ? x.filter((id) => id !== q.id)
                          : [...x, q.id],
                      )
                    }
                  />
                  <QuestionCard q={q} mode="teacher" />
                </div>
              ))
            ) : (
              <p>
                {source === "bank"
                  ? "No published questions match these filters yet."
                  : "Approve questions from an imported paper before building a test."}
              </p>
            )}
          </div>
          {source === "bank" && (
            <div className="review-actions">
              <small>{remote.total} matching questions</small>
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
                disabled={!remote.has_more}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </button>
            </div>
          )}
        </Card>
        <Card className="builder-selection">
          <div className="builder-selection-heading">
            <span>Selection tray</span>
            <b>{selected.length.toString().padStart(2, "0")}</b>
          </div>
          <div className="builder-selection-summary">
            <span><small>Questions</small><b>{selected.length}</b></span>
            <span><small>Source</small><b>{source === "bank" ? "Bank" : "Mine"}</b></span>
          </div>
          <div className="builder-selection-list">
            {selected.length ? <>
              <div className="selection-preview">
                {selected.slice(0, 4).map((id, index) => {
                  const question = selectedQuestions.find((item) => item.id === id);
                  return <span key={id}>{questionNumber(question?.source_number || String(index + 1))}</span>;
                })}
                {selected.length > 4 && <span>+{selected.length - 4} more</span>}
              </div>
              <details className="selection-details">
                <summary>Manage selected questions</summary>
                <div>{selected.map((id, index) => {
                  const question = selectedQuestions.find((item) => item.id === id);
                  return <span key={id}><b>{questionNumber(question?.source_number || String(index + 1))}</b><button type="button" className="text-danger" onClick={() => setSelected((current) => current.filter((questionId) => questionId !== id))}>Remove</button></span>;
                })}</div>
              </details>
            </> : <p>Add questions from the library to build your test.</p>}
          </div>
          <button className="builder-selection-submit" disabled={!selected.length}>
            Create draft{selected.length ? ` (${selected.length})` : ""}
          </button>
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
            <span>Pending tests</span>
          </Card>
          <Card>
            <span className="stat-icon progress">↗</span>
            <b>{inProgress}</b>
            <span>In progress</span>
          </Card>
        </div>
      </section>
      <div className="assessment-heading">
        <div>
          <p className="eyebrow">MY ASSESSMENTS</p>
          <h2>Assigned assessments</h2>
        </div>
        <span>{tests.length} total</span>
      </div>
      <div className="cards student-test-cards">
        {tests.length ? (
          tests.map((t) => {
            const latest = t.latest_attempt;
            const submitted = latest?.status === "submitted";
            const inProgress = latest?.status === "in_progress";
            const canStart =
              t.availability === "available" &&
              (inProgress || Boolean(t.attempts_remaining));
            return (
              <Card key={t.id}>
                <div className="test-card-top">
                  <span
                    className={`badge ${submitted ? "submitted" : inProgress ? "in_progress" : t.availability}`}
                  >
                    {submitted
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
                    {latest.score ?? "—"}
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
                        : submitted
                          ? "Start another attempt"
                          : "Start assessment"}
                    </button>
                  )}
                  {!canStart && !submitted && (
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
  return (
    <div className="file-pickers">
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
    </div>
  );
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
}) {
  const groups = (jobs || []).reduce<Record<string, ImportJob[]>>(
    (all, job) => {
      const subject = job.meta?.subject || "Unclassified";
      (all[subject] ||= []).push(job);
      return all;
    },
    {},
  );
  const allJobs = jobs || [],
    failed = allJobs.filter((job) => job.status === "failed").length,
    active = allJobs.filter(
      (job) => job.status === "uploaded" || job.status === "processing",
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
          <span>
            <b>{allJobs.length}</b> total
          </span>
          <span className={failed ? "needs-action" : ""}>
            <b>{failed}</b> need attention
          </span>
          <span>
            <b>{active}</b> in progress
          </span>
        </div>
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
              const paused = Boolean(job.meta?.pause_requested),
                running =
                  job.status === "uploaded" || job.status === "processing",
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
                      <b>{job.filename || "Untitled import"}</b>
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
                      {job.errors?.length ? <details className="import-error-log">
                        <summary>Recent extraction issues ({job.errors.length})</summary>
                        <p>{job.meta?.failed_pages?.length || job.errors.length} source page range{(job.meta?.failed_pages?.length || job.errors.length) === 1 ? "" : "s"} skipped. Questions may be missing from those ranges; extraction is continuing.</p>
                        <ul>{job.errors.map((error,index)=><li key={`${error.page||"job"}-${index}`}>
                          {error.page ? error.end_page && error.end_page > error.page ? `Pages ${error.page}–${error.end_page}: ` : `Page ${error.page}: ` : ""}{error.message}
                        </li>)}</ul>
                      </details> : null}
                    </div>
                  </div>
                  <div className="import-controls">
                    <span
                      className={`badge ${job.published ? "published" : job.status}`}
                    >
                      {job.published ? "live" : paused ? "paused" : job.status}
                    </span>
                    {failedJob && !job.meta?.cancel_requested && (
                      <button type="button" onClick={() => onRetry(job)}>
                        Retry import
                      </button>
                    )}
                    <button
                      type="button"
                      className="secondary-action"
                      onClick={() => onInspect(job)}
                    >
                      {job.status === "review" ? "Review" : "Inspect"}
                    </button>
                    {!job.published && Boolean(job.approved_count) && (
                      <button type="button" onClick={() => onPublish(job)}>
                        {job.status === "approved" ? "Confirm & publish" : "Publish extracted questions"}
                      </button>
                    )}
                    {running && !paused && (
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
                    {running && !job.meta?.cancel_requested && (
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
                    {canManageStoredOutput && (
                      <button
                        type="button"
                        className="text-danger"
                        title="Delete this source material and every extracted question"
                        aria-label={`Delete ${job.filename || "import"}`}
                        onClick={() => onDelete(job)}
                      >
                        Delete material
                      </button>
                    )}
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
            <b>No source documents yet</b>
            <p>
              Upload a question paper or solutions file to begin building this
              bank.
            </p>
          </div>
        </div>
      )}
    </Card>
  );
}

function QuestionBankManager({
  notify,
}: {
  notify: (message: string) => void;
}) {
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
  const load = async () => setBanks(await api("/admin/question-banks"));
  const open = async (id: string) => {
    setError("");
    const changingBank = detail?.id !== id;
    setDetail(await api(`/admin/question-banks/${id}`));
    setEditing(false);
    setEditingQuestion(null);
    if (changingBank || editingQuestion) {
      setSelectedImport(null);
      setSelectedQuestions(null);
    }
  };
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    const active = detail?.imports?.some(
      (job) =>
        (job.status === "uploaded" || job.status === "processing") &&
        !job.meta?.pause_requested,
    );
    if (!detail?.id || !active || figureEditing) return;
    const timer = window.setInterval(() => {
      api(`/admin/question-banks/${detail.id}`)
        .then(setDetail)
        .catch((e) => setError(e.message));
      load().catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [detail?.id, detail?.imports, figureEditing]);
  useEffect(() => {
    if (!detail?.id || !figureRevalidation || !["queued", "running"].includes(figureRevalidation.status)) return;
    const timer = window.setInterval(() => {
      api(`/admin/question-banks/${detail.id}/figure-revalidation`).then(setFigureRevalidation).catch((e) => setError(e.message));
    }, 3000);
    return () => window.clearInterval(timer);
  }, [detail?.id, figureRevalidation?.status]);
  const revalidateFigures = async () => {
    if (!detail) return;
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
    if (!detail) return;
    const form = e.currentTarget;
    setBusy(true);
    try {
      await api(`/admin/question-banks/${detail.id}`, {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      await load();
      await open(detail.id);
      notify("Bank details saved.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const upload = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!detail) return;
    const form = e.currentTarget;
    setBusy(true);
    setUploadProgress({ loaded: 0, total: 0 });
    try {
      await uploadBankImport(detail.id, bankImportForm(form), (loaded, total) =>
        setUploadProgress({ loaded, total }),
      );
      form.reset();
      await open(detail.id);
      await load();
      notify("Sources queued for extraction.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  };
  const retryImport = async (job: ImportJob) => {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${job.id}/retry`, {
        method: "POST",
      });
      await open(detail.id);
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
    if (!detail) return;
    setBusy(true);
    try {
      await api(
        `/admin/question-banks/${detail.id}/imports/${job.id}/${action}`,
        { method: "POST" },
      );
      await open(detail.id);
      notify(`${job.filename} ${action} requested.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const deleteImport = async (job: ImportJob) => {
    if (
      !detail ||
      !window.confirm(
        `Remove ${job.filename} and its extracted questions from this bank?`,
      )
    )
      return;
    setBusy(true);
    try {
      await api(`/admin/question-banks/${detail.id}/imports/${job.id}`, {
        method: "DELETE",
      });
      setSelectedImport(null);
      await open(detail.id);
      await load();
      notify("Source removed from this bank.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const reextractImport = async (job: ImportJob) => {
    if (
      !detail ||
      !window.confirm(
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
      await open(detail.id);
      notify(`${job.filename || "Source"} queued for clean re-extraction.`);
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
      await api(`/admin/question-banks/${detail.id}/publish`, {
        method: "POST",
      });
      await open(detail.id);
      await load();
      notify("Question bank published to teachers.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const publishImport = async (job: ImportJob) => {
    if (!detail) return;
    setBusy(true);
    try {
      await api(
        `/admin/question-banks/${detail.id}/imports/${job.id}/publish`,
        { method: "POST" },
      );
      await open(detail.id);
      await load();
      notify(`${job.filename || "Source document"} is now live to teachers.`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const revision = async () => {
    if (!detail) return;
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
      !detail ||
      !window.confirm(`Remove ${detail.name} from the question-bank library?`)
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
    if (!detail) return;
    setBusy(true);
    try {
      const report = await api(
        `/admin/question-banks/${detail.id}/imports/${job.id}`,
      );
      setSelectedImport(report);
      setSelectedQuestions(report.questions || []);
      setEditingQuestion(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
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
                onClick={() => open(bank.id)}
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
              <p className="bank-list-empty">No banks match this filter.</p>
            )}
          </div>
        </Card>
      </div>
      <section className="bank-detail-workspace">
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
                    <button type="button" onClick={publish} disabled={busy}>Publish all ready materials</button>
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
                <p>Each source document stays private until confirmed. Publish all ready materials takes every fully reviewed document live; unfinished documents remain in review.</p>
              </Card>
              <Card className="bank-upload-card">
                <h2>Upload source documents</h2>
                <form onSubmit={upload} className="stacked-form">
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
                  <button disabled={busy}>
                    {busy ? "Uploading…" : "Upload and start extraction"}
                  </button>
                </form>
              </Card>
              <BankImportStatus
                jobs={detail.imports}
                onRetry={retryImport}
                onPause={(job) => importAction(job, "pause")}
                onResume={(job) => importAction(job, "resume")}
                onCancel={(job) => importAction(job, "cancel")}
                onDelete={deleteImport}
                onReextract={reextractImport}
                onInspect={inspectImport}
                onPublish={publishImport}
              />
              <Card>
                <h2>
                  {selectedImport
                    ? `Review: ${selectedImport.filename}`
                    : "Review and edit questions"}
                </h2>
                {selectedImport && (
                  <button
                    className="secondary-action"
                    onClick={() => {
                      setSelectedImport(null);
                      setSelectedQuestions(null);
                    }}
                  >
                    Show all documents
                  </button>
                )}
                <p>
                  {selectedImport
                    ? `Showing ${visibleQuestions?.length || 0} questions`
                    : `Showing the latest ${visibleQuestions?.length || 0} of ${detail.question_count} questions`}
                  {selectedImport ? " from this document" : " from this bank"},
                  organized by topic. Open a topic to browse its questions.
                </p>
                {visibleQuestions?.length ? (
                  <>
                    <TopicQuestionBrowser
                      questions={visibleQuestions}
                      activeQuestion={editingQuestion}
                      edit={setEditingQuestion}
                      renderEditor={(question) => (
                        <BankQuestionEditor
                          key={question.id}
                          bankId={detail.id}
                          question={question}
                          done={async () => {
                            setFigureEditing(false);
                            setEditingQuestion(null);
                            await open(detail.id);
                          }}
                          cancel={() => { setFigureEditing(false); setEditingQuestion(null); }}
                          onFigureActive={setFigureEditing}
                        />
                      )}
                    />
                    <AttentionQuestionQueue
                      questions={visibleQuestions}
                      activeQuestion={editingQuestion}
                      edit={setEditingQuestion}
                      renderEditor={(question) => (
                        <BankQuestionEditor
                          key={question.id}
                          bankId={detail.id}
                          question={question}
                          done={async () => {
                            setFigureEditing(false);
                            setEditingQuestion(null);
                            await open(detail.id);
                          }}
                          cancel={() => { setFigureEditing(false); setEditingQuestion(null); }}
                          onFigureActive={setFigureEditing}
                        />
                      )}
                    />
                  </>
                ) : (
                  <p>
                    {selectedImport?.status === "failed"
                      ? selectedImport.error ||
                        "This source failed before questions could be extracted."
                      : "Questions will appear after extraction."}
                  </p>
                )}
              </Card>
            </>
          ) : (
            <Card>Create or select a bank to begin.</Card>
          )}
      </section>
    </div>
  );
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
  const issues = question.interaction?.ingestion_issues || [];
  return <div className="document-question" key={question.id}>
    <article className="row">
      <div>
        <b>{questionNumber(question.source_number)} · {question.kind}</b>
        <small>Page {question.interaction?.source_page || "—"} · {issues.length ? `Flagged: ${issues.join(", ")}` : question.approved ? "Reviewed" : "Needs review"}</small>
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
      {Object.entries(tree).map(([subject, chapters]) => <details className="taxonomy-subject" key={subject} open><summary><b>{subject}</b><small>{Object.values(chapters).reduce((sum, topics) => sum + Object.values(topics).reduce((count, items) => count + items.length, 0), 0)} questions</small></summary>{Object.entries(chapters).map(([chapter, topics]) => <details className="taxonomy-chapter" key={chapter} open><summary><b>{chapter}</b><small>{Object.values(topics).reduce((sum, items) => sum + items.length, 0)} questions</small></summary>{Object.entries(topics).map(([topic, topicQuestions]) => <details className="taxonomy-topic" key={topic} open={topicQuestions.some((question) => question.id === activeQuestion?.id)}><summary><span><b>{topic}</b><small>{topicQuestions.filter((question) => (question.interaction?.ingestion_issues || []).length || !question.approved).length ? "Needs attention" : "Complete"}</small></span><strong>{topicQuestions.length}</strong></summary><div className="topic-question-list">{topicQuestions.map((question) => <QuestionReviewRow key={question.id} question={question} activeQuestion={activeQuestion} edit={edit} renderEditor={renderEditor} />)}</div></details>)}</details>)}</details>)}
    </section>
  );
}

function AttentionQuestionQueue({questions,activeQuestion,edit,renderEditor}:{questions:Question[];activeQuestion:Question|null;edit:(question:Question)=>void;renderEditor:(question:Question)=>React.ReactNode}){
 const attention=questions.filter(question=>(question.interaction?.ingestion_issues||[]).length||!question.approved);
 return <section className="attention-question-queue"><div className="question-browser-heading"><div><p className="eyebrow">REQUIRES REVIEW</p><h3>Questions needing attention</h3><p>Only flagged or unresolved questions appear here.</p></div><span className={attention.length?"needs-attention":""}>{attention.length} items</span></div>{attention.length?<div className="document-question-list">{attention.map(question=><QuestionReviewRow key={question.id} question={question} activeQuestion={activeQuestion} edit={edit} renderEditor={renderEditor}/>)}</div>:<p className="queue-empty">No issues found. All questions are classified and reviewed.</p>}</section>
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
function BankQuestionEditor({
  bankId,
  question,
  done,
  cancel,
  onFigureActive,
}: {
  bankId: string;
  question: Question;
  done: () => Promise<void>;
  cancel: () => void;
  onFigureActive?: (active: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [tab, setTab] = useState<"rendered" | "json" | "edit">("rendered");
  const suggestedAnswer = inferredChoiceFromSolution(question);
  const ignoreWarningsAndApprove = async () => {
    setBusy(true);
    setError("");
    try {
      await api(
        `/admin/question-banks/${bankId}/questions/${question.id}/resolve?ignore_warnings=true`,
        {
          method: "POST",
          body: JSON.stringify({
            source_number: question.source_number,
            kind: question.kind,
            stem_markdown: question.stem_markdown,
            options: question.options,
            answer: suggestedAnswer ?? question.answer,
            solution_markdown: question.solution_markdown || null,
            scoring: question.scoring || {
              correct: 4,
              incorrect: -1,
              unanswered: 0,
            },
            confidence: question.confidence,
          }),
        },
      );
      await done();
    } catch (e: any) {
      setError(e.message || "This question still needs an answer before approval.");
    } finally {
      setBusy(false);
    }
  };
  const save = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const action = (e.nativeEvent as SubmitEvent).submitter?.getAttribute("data-action");
    const approve = action !== "draft";
    const ignoreWarnings = action === "ignore-warnings";
    setBusy(true);
    setError("");
    try {
      const options = JSON.parse(String(form.get("options") || "[]"));
      const answer = JSON.parse(String(form.get("answer") || "null"));
      await api(
        approve ? `/admin/question-banks/${bankId}/questions/${question.id}/resolve${ignoreWarnings ? "?ignore_warnings=true" : ""}` : `/questions/${question.id}/draft`,
        {
          method: approve ? "POST" : "PUT",
          body: JSON.stringify({
            source_number: question.source_number,
            kind: form.get("kind"),
            stem_markdown: form.get("stem_markdown"),
            options,
            answer,
            solution_markdown: form.get("solution_markdown") || null,
            scoring: question.scoring || {
              correct: 4,
              incorrect: -1,
              unanswered: 0,
            },
            confidence: Number(form.get("confidence")) / 100,
          }),
        },
      );
      await done();
    } catch (e: any) {
      setError(e.message || "Check the answer and JSON fields.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className="review-question bank-question-review">
      <div className="bank-review-heading">
        <div>
          <h3>{questionNumber(question.source_number)} review</h3>
          <p>
            <small>Source: {sourceLabel(question)}</small>
          </p>
        </div>
        <span className={`badge ${question.approved ? "approved" : "review"}`}>
          {question.approved ? "Reviewed" : "Needs review"}
        </span>
      </div>
      <nav
        className="review-tabs"
        aria-label={`Question ${questionNumber(question.source_number)} view`}
      >
        <button
          type="button"
          className={tab === "rendered" ? "active" : ""}
          onClick={() => setTab("rendered")}
        >
          Rendered
        </button>
        <button
          type="button"
          className={tab === "json" ? "active" : ""}
          onClick={() => setTab("json")}
        >
          JSON
        </button>
        <button
          type="button"
          className={tab === "edit" ? "active" : ""}
          onClick={() => setTab("edit")}
        >
          Edit
        </button>
      </nav>
      {tab === "rendered" && (
        <div className="bank-rendered-question">
          <QuestionCard q={question} mode="teacher" />
          {needsFigureAdjustment(question) && <FigureEditor questionId={question.id} options={question.options} api={api} done={done} onActiveChange={onFigureActive} />}
          {!!(question.interaction?.ingestion_issues || []).length && (
            <div className="review-buttons warning-override-action">
              <button type="button" className="secondary-action" disabled={busy} onClick={ignoreWarningsAndApprove}>
                {busy ? "Saving…" : "Ignore warnings & approve"}
              </button>
              <small>Approves this question for publication and records the warnings in its audit trail.</small>
            </div>
          )}
        </div>
      )}
      {tab === "json" && (
        <div className="bank-json-panel">
          <p>
            Exact stored extraction payload. This is read-only; use Edit to
            correct and approve it.
          </p>
          <pre>{JSON.stringify(question, null, 2)}</pre>
        </div>
      )}
      {tab === "edit" && (
        <form className="stacked-form" onSubmit={save}>
          <label>
            Type
            <select name="kind" defaultValue={question.kind}>
              {JEE_QUESTION_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Question (Markdown / LaTex)
            <textarea
              name="stem_markdown"
              defaultValue={question.stem_markdown}
            />
          </label>
          <label>
            Options (JSON)
            <textarea
              name="options"
              defaultValue={JSON.stringify(question.options, null, 2)}
            />
          </label>
          <label>
            Answer (JSON)
            <textarea
              name="answer"
              defaultValue={JSON.stringify(suggestedAnswer ?? null, null, 2)}
            />
          </label>
          {!question.answer && suggestedAnswer && (
            <small>
              The answer key was inferred from the extracted solution. Verify it
              before saving.
            </small>
          )}
          {!question.answer && !suggestedAnswer && (
            <small>
              An answer key is genuinely missing. Add one before approval.
            </small>
          )}
          <label>
            Solution (Markdown / LaTex)
            <textarea
              name="solution_markdown"
              defaultValue={question.solution_markdown || ""}
            />
          </label>
          <label>
            Confidence %
            <input
              name="confidence"
              type="number"
              min="0"
              max="100"
              defaultValue={Math.round((question.confidence ?? 0) * 100)}
            />
          </label>
          <div className="review-buttons">
            <button type="submit" data-action="draft" className="secondary-action" disabled={busy}>Save draft</button>
            <button type="submit" data-action="approve" disabled={busy}>
              {busy ? "Saving…" : "Save and approve"}
            </button>
            {!!(question.interaction?.ingestion_issues || []).length && (
              <button type="submit" data-action="ignore-warnings" className="secondary-action" disabled={busy}>
                Ignore warnings & approve
              </button>
            )}
            <button type="button" className="secondary-action" onClick={cancel}>
              Cancel
            </button>
          </div>
          {error && <Notice kind="error">{error}</Notice>}
        </form>
      )}
    </article>
  );
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
}: {
  q: Question;
  mode?: "teacher" | "student-result" | "exam";
  studentAnswer?: any;
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
        {questionNumber(q.source_number)} · {q.kind.replaceAll("_", " ")}
      </b>
      <Markdown value={q.stem_markdown} />
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
                <span className="answer-badge">Correct answer</span>
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
function Exam({
  attempt,
  index,
  setIndex,
  update,
  finish,
}: {
  attempt: any;
  index: number;
  setIndex: (n: number) => void;
  update: (q: string, a: any, m: boolean) => void;
  finish: () => void;
}) {
  const q = attempt.questions[index],
    r = attempt.responses[q.id] || {},
    [now, setNow] = useState(Date.now());
  useEffect(() => {
    const x = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(x);
  }, []);
  const left = Math.max(0, new Date(attempt.ends_at).getTime() - now);
  useEffect(() => {
    if (!left && !attempt.submitted_at) finish();
  }, [left]);
  const multiple = q.kind === "multiple_choice";
  const matrix = q.kind === "matrix_match";
  const written = ["numerical", "integer", "subjective"].includes(q.kind);
  const toggle = (i: number) => {
    const selected: Array<number> = Array.isArray(r.answer?.options)
      ? r.answer.options
      : [];
    const next = selected.includes(i)
      ? selected.filter((x) => x !== i)
      : [...selected, i].sort((a, b) => a - b);
    update(q.id, { options: next }, r.marked_for_review);
  };
  const toggleMatch = (row: string, choice: string) => {
    const matches: Record<string, string[]> = { ...(r.answer?.matches || {}) };
    const selected = matches[row] || [];
    matches[row] = selected.includes(choice)
      ? selected.filter((x) => x !== choice)
      : [...selected, choice].sort();
    update(q.id, { matches }, r.marked_for_review);
  };
  return (
    <div className="exam">
      <header>
        <b>TestNow Exam Console</b>
        <strong>
          {Math.floor(left / 60000)}:
          {String(Math.floor(left / 1000) % 60).padStart(2, "0")}
        </strong>
      </header>
      <aside>
        {attempt.questions.map((x: any, i: number) => (
          <button
            className={`${i === index ? "active" : ""} ${attempt.responses[x.id]?.answer ? "answered" : ""}`}
            key={x.id}
            onClick={() => setIndex(i)}
          >
            {i + 1}
          </button>
        ))}
      </aside>
      <section>
        <p>
          QUESTION {index + 1} OF {attempt.questions.length}
        </p>
        <div className="exam-scroll">
          <QuestionCard q={q} mode="exam" />
          <div className="answers">
            {written ? (
              <textarea
                aria-label="Your answer"
                value={r.answer?.value ?? ""}
                onChange={(e) =>
                  update(q.id, { value: e.target.value }, r.marked_for_review)
                }
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
                              (r.answer?.matches?.[row] || []).includes(choice)
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
                  ? (r.answer?.options || []).includes(i)
                  : JSON.stringify(r.answer) === JSON.stringify({ option: i });
                return (
                  <button
                    key={i}
                    className={selected ? "chosen" : ""}
                    onClick={() =>
                      multiple
                        ? toggle(i)
                        : update(q.id, { option: i }, r.marked_for_review)
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
          <button onClick={() => update(q.id, null, r.marked_for_review)}>
            Clear response
          </button>
          <button onClick={() => update(q.id, r.answer, !r.marked_for_review)}>
            {r.marked_for_review ? "Unmark review" : "Mark for review"}
          </button>
          <button
            onClick={() =>
              setIndex(Math.min(index + 1, attempt.questions.length - 1))
            }
          >
            Save & next
          </button>
          <button className="danger" onClick={finish}>
            Submit test
          </button>
        </footer>
      </section>
    </div>
  );
}
function Result({ attempt }: { attempt: any }) {
  const [data, setData] = useState<any>();
  useEffect(() => {
    api(`/results/${attempt.id}`)
      .then(setData)
      .catch((e) => setData({ error: e.message }));
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
      {data.questions.map(
        (q: Question & { response: any; correct: boolean; points: number }) => (
          <Card key={q.id}>
            <QuestionCard
              q={q}
              mode="student-result"
              studentAnswer={q.response}
            />
            <p>
              Your response:{" "}
              {q.response?.option !== undefined
                ? `Option ${String.fromCharCode(65 + q.response.option)}`
                : "No response"}{" "}
              · {q.correct ? "Correct" : "Incorrect"} · {q.points} marks
            </p>
          </Card>
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
      await api(`/admin/question-banks/${detail.id}/publish`, {
        method: "POST",
      });
      await open(detail.id);
      await load();
      notify("Question bank published to teachers.");
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
