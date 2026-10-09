#!/usr/bin/env python3
"""TestNow local application server. Run: python3 server.py"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote, urlparse
from email.parser import BytesParser
from email.policy import default
import base64, hashlib, io, json, mimetypes, os, re, secrets, shutil, sqlite3, subprocess, sys, time

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "testnow.db"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)
SESSIONS = {}

BANK = [
 ("Kinematics","MCQ","Easy","A particle moves with uniform acceleration. Which graph is linear?",["Velocity vs time","Displacement vs time","Acceleration vs time squared","None"],"0","Velocity changes linearly for constant acceleration."),
 ("Laws of Motion","MCQ","Medium","A 2 kg body experiences a net force of 10 N. Its acceleration is:",["2 m/s²","5 m/s²","10 m/s²","20 m/s²"],"1","F = ma, so a = 10/2 = 5 m/s²."),
 ("Work & Energy","Numerical","Medium","A 5 kg body is lifted through 4 m. Take g = 10 m/s². Enter the work done (J).",[],"200","Work = mgh = 5 × 10 × 4 = 200 J."),
 ("Electrostatics","MCQ","Easy","The SI unit of electric charge is:",["Coulomb","Tesla","Volt","Ohm"],"0","Electric charge is measured in coulombs."),
 ("Current Electricity","MCQ","Medium","Two 6 Ω resistors in parallel have equivalent resistance:",["12 Ω","6 Ω","3 Ω","1.5 Ω"],"2","For equal parallel resistors R/2 = 3 Ω."),
 ("Magnetism","MCQ","Hard","A charged particle moving parallel to a magnetic field experiences:",["maximum force","zero force","circular motion","variable force"],"1","Magnetic force q(v × B) is zero when v is parallel to B."),
 ("Optics","Numerical","Easy","A convex lens has focal length 20 cm. Enter its power in dioptres.",[],"5","P = 1/f = 1/0.20 = 5 D."),
 ("Modern Physics","MCQ","Medium","Photoelectric emission supports the:",["wave nature only","particle nature of light","nuclear model","law of inertia"],"1","Photoelectric effect demonstrates photons."),
 ("Thermodynamics","MCQ","Medium","For an ideal gas at constant pressure, heat supplied is used for:",["internal energy only","work only","both internal energy and work","neither"],"2","At constant pressure Q = ΔU + W."),
 ("Waves","MCQ","Easy","The frequency of a wave is measured in:",["metre","second","hertz","radian"],"2","Hertz is cycles per second."),
]

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def rows(cur): return [dict(x) for x in cur.fetchall()]
def password(value, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode(), salt.encode(), 180000).hex()
    return f"{salt}${digest}"
def password_ok(value, stored):
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(password(value, salt), stored)

def init_db():
    con = db()
    con.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('teacher','student')), created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY, owner_id INTEGER, source TEXT NOT NULL, chapter TEXT NOT NULL, kind TEXT NOT NULL, difficulty TEXT NOT NULL, prompt TEXT NOT NULL, options_json TEXT NOT NULL, answer TEXT NOT NULL, solution TEXT NOT NULL, visual_paths TEXT NOT NULL DEFAULT '[]', created_at INTEGER NOT NULL, FOREIGN KEY(owner_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS tests(id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL, subject TEXT NOT NULL, duration_minutes INTEGER NOT NULL, marks INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', paper_filename TEXT, key_filename TEXT, created_at INTEGER NOT NULL, FOREIGN KEY(owner_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS test_questions(test_id INTEGER NOT NULL, question_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(test_id,question_id), FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE, FOREIGN KEY(question_id) REFERENCES questions(id));
    CREATE TABLE IF NOT EXISTS enrollments(test_id INTEGER NOT NULL, student_id INTEGER NOT NULL, PRIMARY KEY(test_id,student_id), FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE, FOREIGN KEY(student_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, test_id INTEGER NOT NULL, student_id INTEGER NOT NULL, started_at INTEGER NOT NULL, submitted_at INTEGER, remaining_seconds INTEGER NOT NULL, score INTEGER, FOREIGN KEY(test_id) REFERENCES tests(id), FOREIGN KEY(student_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS answers(attempt_id INTEGER NOT NULL, question_id INTEGER NOT NULL, response TEXT, reviewed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(attempt_id,question_id), FOREIGN KEY(attempt_id) REFERENCES attempts(id) ON DELETE CASCADE, FOREIGN KEY(question_id) REFERENCES questions(id));
    """)
    columns={row['name'] for row in con.execute("PRAGMA table_info(questions)")}
    if 'visual_paths' not in columns: con.execute("ALTER TABLE questions ADD COLUMN visual_paths TEXT NOT NULL DEFAULT '[]'")
    teacher = con.execute("SELECT id FROM users WHERE email=?", ("teacher@testnow.local",)).fetchone()
    if not teacher:
        now = int(time.time())
        teacher_id = con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)", ("Ananya Khanna","teacher@testnow.local",password("teacher123"),"teacher",now)).lastrowid
        con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)", ("Rohan Sharma","student@testnow.local",password("student123"),"student",now))
        for i in range(25):
            ch, kind, diff, prompt, options, ans, solution = BANK[i % len(BANK)]
            con.execute("INSERT INTO questions(owner_id,source,chapter,kind,difficulty,prompt,options_json,answer,solution,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (teacher_id, "JEE Main PYQ 2024" if i < 13 else "Physics Practice Sheet", ch, kind, diff, prompt, json.dumps(options), ans, solution, now))
        test_id = con.execute("INSERT INTO tests(owner_id,title,subject,duration_minutes,marks,status,created_at) VALUES(?,?,?,?,?,?,?)", (teacher_id,"Test A · Physics Mechanics","Physics",60,75,"live",now)).lastrowid
        question_ids = con.execute("SELECT id FROM questions ORDER BY id LIMIT 25").fetchall()
        con.executemany("INSERT INTO test_questions(test_id,question_id,ordinal) VALUES(?,?,?)", [(test_id,x[0],n+1) for n,x in enumerate(question_ids)])
        student = con.execute("SELECT id FROM users WHERE email=?", ("student@testnow.local",)).fetchone()[0]
        con.execute("INSERT INTO enrollments(test_id,student_id) VALUES(?,?)", (test_id,student))
    con.commit(); con.close()

def as_question(row, include_answer=False):
    d=dict(row); d['options']=json.loads(d.pop('options_json')); d['visual_paths']=json.loads(d.get('visual_paths') or '[]')
    if not include_answer: d.pop('answer',None); d.pop('solution',None)
    return d

class API(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def log_message(self, fmt, *args): print("[TestNow]", fmt % args)
    def user(self):
        cookie=SimpleCookie(self.headers.get('Cookie')); token=cookie.get('testnow_session')
        return SESSIONS.get(token.value) if token else None
    def require(self, role=None):
        user=self.user()
        if not user or (role and user['role'] != role): self.json({"error":"Authentication required"}, 401); return None
        return user
    def json(self, value, status=200, cookie=None):
        raw=json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw)))
        if cookie: self.send_header("Set-Cookie",cookie)
        self.end_headers(); self.wfile.write(raw)
    def body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0) or b'{}')
        except json.JSONDecodeError: return {}
    def multipart(self):
        length=int(self.headers.get('Content-Length','0')); raw=self.rfile.read(length)
        content=b"Content-Type: "+self.headers['Content-Type'].encode()+b"\r\nMIME-Version: 1.0\r\n\r\n"+raw
        message=BytesParser(policy=default).parsebytes(content); output={}
        for part in message.iter_parts():
            cd=part.get('Content-Disposition',''); match=re.search(r'name="([^"]+)"',cd)
            if not match: continue
            name=match.group(1); filename=part.get_filename(); data=part.get_payload(decode=True) or b''
            output[name]={'filename':filename,'data':data} if filename else data.decode(errors='replace')
        return output
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/server.py','/testnow.db','/.gitignore') or path.startswith('/uploads/') or path.startswith('/.git'):
            return self.json({'error':'Not found'},404)
        if path == '/api/me':
            return self.json({"user":self.user()})
        if path == '/api/teacher/tests': return self.teacher_tests()
        if path == '/api/question-bank': return self.question_bank()
        if path == '/api/student/tests': return self.student_tests()
        if path.startswith('/api/media/'): return self.media(unquote(path.removeprefix('/api/media/')))
        match=re.fullmatch(r'/api/tests/(\d+)',path)
        if match: return self.test_detail(int(match.group(1)))
        match=re.fullmatch(r'/api/attempts/(\d+)',path)
        if match: return self.attempt_detail(int(match.group(1)))
        match=re.fullmatch(r'/api/attempts/(\d+)/review',path)
        if match: return self.attempt_review(int(match.group(1)))
        return super().do_GET()
    def media(self, relative):
        if not self.require(): return
        candidate=(UPLOADS / relative).resolve()
        if UPLOADS.resolve() not in candidate.parents or not candidate.is_file(): return self.json({'error':'Not found'},404)
        mime=mimetypes.guess_type(candidate.name)[0] or 'application/octet-stream'
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(candidate.stat().st_size)); self.end_headers()
        with candidate.open('rb') as source: shutil.copyfileobj(source,self.wfile)
    def do_POST(self):
        path=urlparse(self.path).path
        if path == '/api/auth/register': return self.register()
        if path == '/api/auth/login': return self.login()
        if path == '/api/auth/logout':
            token=SimpleCookie(self.headers.get('Cookie')).get('testnow_session');
            if token: SESSIONS.pop(token.value,None)
            return self.json({"ok":True},cookie='testnow_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
        if path == '/api/upload': return self.upload()
        if path == '/api/import-paper': return self.import_paper()
        if path == '/api/tests': return self.create_test()
        match=re.fullmatch(r'/api/tests/(\d+)/enroll',path)
        if match: return self.enroll(int(match.group(1)))
        match=re.fullmatch(r'/api/tests/(\d+)/attempts',path)
        if match: return self.start_attempt(int(match.group(1)))
        match=re.fullmatch(r'/api/attempts/(\d+)/answer',path)
        if match: return self.save_answer(int(match.group(1)))
        match=re.fullmatch(r'/api/attempts/(\d+)/submit',path)
        if match: return self.submit_attempt(int(match.group(1)))
        self.json({"error":"Not found"},404)
    def register(self):
        data=self.body(); name=data.get('name','').strip(); email=data.get('email','').lower().strip(); role=data.get('role'); pw=data.get('password','')
        if not name or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email) or role not in ('teacher','student') or len(pw)<8: return self.json({"error":"Use a name, valid email, role, and an 8+ character password."},400)
        try:
            con=db(); uid=con.execute("INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)",(name,email,password(pw),role,int(time.time()))).lastrowid; con.commit();con.close()
        except sqlite3.IntegrityError: return self.json({"error":"An account with that email already exists."},409)
        return self.make_session({'id':uid,'name':name,'email':email,'role':role})
    def login(self):
        data=self.body(); con=db(); row=con.execute("SELECT * FROM users WHERE email=?",(data.get('email','').lower().strip(),)).fetchone();con.close()
        if not row or not password_ok(data.get('password',''),row['password_hash']): return self.json({"error":"Incorrect email or password."},401)
        return self.make_session({k:row[k] for k in ('id','name','email','role')})
    def make_session(self,user):
        token=secrets.token_urlsafe(32); SESSIONS[token]=user; self.json({"user":user},cookie=f'testnow_session={token}; Path=/; HttpOnly; SameSite=Lax')
    def teacher_tests(self):
        user=self.require('teacher');
        if not user:return
        con=db(); result=rows(con.execute("SELECT t.*,COUNT(tq.question_id) question_count FROM tests t LEFT JOIN test_questions tq ON tq.test_id=t.id WHERE t.owner_id=? GROUP BY t.id ORDER BY t.created_at DESC",(user['id'],)));con.close();self.json({'tests':result})
    def question_bank(self):
        user=self.require('teacher');
        if not user:return
        con=db(); qs=[as_question(x,True) for x in con.execute("SELECT * FROM questions WHERE owner_id IS NULL OR owner_id=? ORDER BY id DESC",(user['id'],))];con.close();self.json({'questions':qs})
    def student_tests(self):
        user=self.require('student');
        if not user:return
        con=db(); result=rows(con.execute("SELECT t.*,COUNT(tq.question_id) question_count,a.id attempt_id,a.submitted_at FROM tests t JOIN enrollments e ON e.test_id=t.id LEFT JOIN test_questions tq ON tq.test_id=t.id LEFT JOIN attempts a ON a.test_id=t.id AND a.student_id=? WHERE e.student_id=? AND t.status='live' GROUP BY t.id ORDER BY t.created_at DESC",(user['id'],user['id'])));con.close();self.json({'tests':result})
    def test_detail(self,test_id):
        user=self.require();
        if not user:return
        con=db(); test=con.execute("SELECT * FROM tests WHERE id=?",(test_id,)).fetchone()
        allowed=test and (test['owner_id']==user['id'] or con.execute("SELECT 1 FROM enrollments WHERE test_id=? AND student_id=?",(test_id,user['id'])).fetchone())
        if not allowed: con.close();return self.json({'error':'Not found'},404)
        qs=[as_question(x,user['role']=='teacher') for x in con.execute("SELECT q.* FROM questions q JOIN test_questions tq ON tq.question_id=q.id WHERE tq.test_id=? ORDER BY tq.ordinal",(test_id,))];con.close();self.json({'test':dict(test),'questions':qs})
    def upload(self):
        user=self.require('teacher');
        if not user:return
        if 'multipart/form-data' not in self.headers.get('Content-Type',''): return self.json({'error':'Use multipart upload.'},400)
        parts=self.multipart(); response={}
        for name in ('paper','answer_key'):
            item=parts.get(name)
            if not item or not item['filename']: continue
            safe=re.sub(r'[^\w.-]','_',Path(item['filename']).name); stored=f"{int(time.time())}_{secrets.token_hex(4)}_{safe}"; path=UPLOADS/stored;path.write_bytes(item['data'])
            text=''
            try:
                from pypdf import PdfReader
                text='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(item['data'])).pages)
            except Exception: text='Text extraction was unavailable for this PDF. You can still use the question-bank review.'
            response[name]={'filename':item['filename'],'stored':stored,'text_preview':text[:1600]}
        if not response:return self.json({'error':'Choose at least one PDF file.'},400)
        self.json({'uploads':response,'suggested_questions':25})
    def import_paper(self):
        user=self.require('teacher')
        if not user:return
        stored=Path(self.body().get('stored','')).name
        source=(UPLOADS / stored).resolve()
        if UPLOADS.resolve() not in source.parents or not source.is_file() or source.suffix.lower() != '.pdf': return self.json({'error':'Choose a stored PDF question paper first.'},400)
        try:
            result=subprocess.run([sys.executable, str(ROOT/'import_pdf.py'), str(source), user['email']],cwd=ROOT,capture_output=True,text=True,check=True,timeout=90)
            self.json(json.loads(result.stdout.strip().splitlines()[-1]),201)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            self.json({'error':f'Paper import failed: {str(exc)[:180]}'},500)
    def create_test(self):
        user=self.require('teacher');
        if not user:return
        d=self.body(); title=d.get('title','').strip(); question_ids=[int(x) for x in d.get('question_ids',[]) if str(x).isdigit()]
        if not title or not question_ids:return self.json({'error':'A title and at least one question are required.'},400)
        con=db(); owned={r[0] for r in con.execute("SELECT id FROM questions WHERE id IN (%s) AND (owner_id IS NULL OR owner_id=?)" % ','.join('?'*len(question_ids)),(*question_ids,user['id']))}
        question_ids=[x for x in question_ids if x in owned]
        tid=con.execute("INSERT INTO tests(owner_id,title,subject,duration_minutes,marks,status,paper_filename,key_filename,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(user['id'],title,d.get('subject','Physics'),max(1,int(d.get('duration_minutes',60))),max(1,int(d.get('marks',len(question_ids)*4))),d.get('status','live'),d.get('paper_filename'),d.get('key_filename'),int(time.time()))).lastrowid
        con.executemany("INSERT INTO test_questions(test_id,question_id,ordinal) VALUES(?,?,?)",[(tid,q,n+1) for n,q in enumerate(question_ids)]);con.commit();con.close();self.json({'test_id':tid},201)
    def enroll(self,test_id):
        user=self.require('teacher');
        if not user:return
        email=self.body().get('email','').lower().strip();con=db(); test=con.execute("SELECT 1 FROM tests WHERE id=? AND owner_id=?",(test_id,user['id'])).fetchone();student=con.execute("SELECT id FROM users WHERE email=? AND role='student'",(email,)).fetchone()
        if not test or not student:con.close();return self.json({'error':'Student account not found or test not owned by you.'},404)
        con.execute("INSERT OR IGNORE INTO enrollments(test_id,student_id) VALUES(?,?)",(test_id,student['id']));con.commit();con.close();self.json({'ok':True})
    def start_attempt(self,test_id):
        user=self.require('student');
        if not user:return
        con=db();test=con.execute("SELECT * FROM tests WHERE id=? AND status='live'",(test_id,)).fetchone();enrolled=con.execute("SELECT 1 FROM enrollments WHERE test_id=? AND student_id=?",(test_id,user['id'])).fetchone()
        if not test or not enrolled:con.close();return self.json({'error':'You are not enrolled in this test.'},403)
        active=con.execute("SELECT id FROM attempts WHERE test_id=? AND student_id=? AND submitted_at IS NULL",(test_id,user['id'])).fetchone()
        aid=active['id'] if active else con.execute("INSERT INTO attempts(test_id,student_id,started_at,remaining_seconds) VALUES(?,?,?,?)",(test_id,user['id'],int(time.time()),test['duration_minutes']*60)).lastrowid
        con.commit();con.close();self.json({'attempt_id':aid})
    def attempt_detail(self,aid):
        user=self.require('student');
        if not user:return
        con=db();a=con.execute("SELECT a.*,t.title,t.subject,t.duration_minutes FROM attempts a JOIN tests t ON t.id=a.test_id WHERE a.id=? AND a.student_id=?",(aid,user['id'])).fetchone()
        if not a:con.close();return self.json({'error':'Not found'},404)
        qs=[as_question(x,False) for x in con.execute("SELECT q.* FROM questions q JOIN test_questions tq ON tq.question_id=q.id WHERE tq.test_id=? ORDER BY tq.ordinal",(a['test_id'],))];answers={x['question_id']:{'response':x['response'],'reviewed':bool(x['reviewed'])} for x in con.execute("SELECT * FROM answers WHERE attempt_id=?",(aid,))};con.close();self.json({'attempt':dict(a),'questions':qs,'answers':answers})
    def save_answer(self,aid):
        user=self.require('student');
        if not user:return
        d=self.body();qid=int(d.get('question_id',0));con=db();a=con.execute("SELECT a.* FROM attempts a WHERE a.id=? AND a.student_id=? AND a.submitted_at IS NULL",(aid,user['id'])).fetchone()
        valid=a and con.execute("SELECT 1 FROM test_questions WHERE test_id=? AND question_id=?",(a['test_id'],qid)).fetchone()
        if not valid:con.close();return self.json({'error':'Invalid attempt or question.'},400)
        con.execute("INSERT INTO answers(attempt_id,question_id,response,reviewed) VALUES(?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET response=excluded.response,reviewed=excluded.reviewed",(aid,qid,None if d.get('response') is None else str(d.get('response')),1 if d.get('reviewed') else 0));con.execute("UPDATE attempts SET remaining_seconds=? WHERE id=?",(max(0,int(d.get('remaining_seconds',a['remaining_seconds']))),aid));con.commit();con.close();self.json({'ok':True})
    def attempt_review(self,aid):
        user=self.require('student')
        if not user:return
        con=db(); a=con.execute("SELECT a.*,t.title FROM attempts a JOIN tests t ON t.id=a.test_id WHERE a.id=? AND a.student_id=? AND a.submitted_at IS NOT NULL",(aid,user['id'])).fetchone()
        if not a: con.close(); return self.json({'error':'Submit the attempt before viewing solutions.'},400)
        saved={x['question_id']:x['response'] for x in con.execute("SELECT * FROM answers WHERE attempt_id=?",(aid,))}; output=[]
        for q in con.execute("SELECT q.* FROM questions q JOIN test_questions tq ON tq.question_id=q.id WHERE tq.test_id=? ORDER BY tq.ordinal",(a['test_id'],)):
            item=as_question(q,True); item['response']=saved.get(q['id']); item['correct']=item['response'] not in (None,'') and str(item['response']).strip()==str(item['answer']).strip(); output.append(item)
        con.close(); self.json({'attempt':dict(a),'answers':output})
    def submit_attempt(self,aid):
        user=self.require('student');
        if not user:return
        con=db();a=con.execute("SELECT * FROM attempts WHERE id=? AND student_id=?",(aid,user['id'])).fetchone()
        if not a:con.close();return self.json({'error':'Not found'},404)
        qs=con.execute("SELECT q.* FROM questions q JOIN test_questions tq ON tq.question_id=q.id WHERE tq.test_id=?",(a['test_id'],)).fetchall();answers={x['question_id']:x['response'] for x in con.execute("SELECT * FROM answers WHERE attempt_id=?",(aid,))};correct=wrong=0
        for q in qs:
            response=answers.get(q['id'])
            if response not in (None,''):
                if str(response).strip()==str(q['answer']).strip():correct+=1
                else:wrong+=1
        score=correct*4-wrong;con.execute("UPDATE attempts SET submitted_at=?,score=? WHERE id=?",(int(time.time()),score,aid));con.commit();con.close();self.json({'score':score,'correct':correct,'wrong':wrong,'total':len(qs)})

if __name__ == '__main__':
    init_db()
    print('TestNow running at http://127.0.0.1:8080')
    ThreadingHTTPServer(('127.0.0.1',8080),API).serve_forever()
