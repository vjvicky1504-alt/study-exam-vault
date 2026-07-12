from flask import Blueprint, render_template, request, jsonify, send_from_directory, current_app, abort, session, flash, redirect
from models import QuestionPaper, AdminNotification, DiscussionPost, StudentAIChat, Student
from extensions import db, csrf
import os
import urllib.request
import urllib.error
import json
import pdfplumber
import io

main_bp = Blueprint('main', __name__)

# ── Canonical class choices (single source of truth) ──────────────────────────
CLASS_CHOICES = ['B.Sc', 'B.Com', 'B.A', 'B.E', 'B.Tech', 'BCA', 'BBA', 'MBA', 'MCA', 'M.Sc']


# ── Home page ──────────────────────────────────────────────────────────────────
@main_bp.route('/')
def index():
    departments = db.session.query(QuestionPaper.department).distinct().order_by(QuestionPaper.department).all()
    semesters   = db.session.query(QuestionPaper.semester).distinct().order_by(QuestionPaper.semester).all()
    exam_types  = db.session.query(QuestionPaper.exam_type).distinct().order_by(QuestionPaper.exam_type).all()
    years       = db.session.query(QuestionPaper.year).distinct().order_by(QuestionPaper.year.desc()).all()
    class_names = (
        db.session.query(QuestionPaper.class_name)
        .filter(QuestionPaper.class_name.isnot(None))
        .distinct()
        .order_by(QuestionPaper.class_name)
        .all()
    )

    return render_template(
        'index.html',
        departments=[d[0] for d in departments],
        semesters=[s[0] for s in semesters],
        exam_types=[e[0] for e in exam_types],
        years=[y[0] for y in years],
        class_names=[c[0] for c in class_names],
    )


# ── AJAX search / filter API ───────────────────────────────────────────────────
@main_bp.route('/api/papers')
def api_papers():
    q          = request.args.get('q', '').strip()
    department = request.args.get('department', '')
    semester   = request.args.get('semester', '')
    class_name = request.args.get('class_name', '')

    query = QuestionPaper.query.filter(
        db.or_(
            QuestionPaper.status == 'approved',
            QuestionPaper.status.is_(None)
        )
    )

    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                QuestionPaper.subject_name.ilike(like),
                QuestionPaper.subject_code.ilike(like),
            )
        )
    if department and department != 'all':
        # Also fetch papers stored as 'All Departments' (e.g. Tamil, English)
        # so common subjects are returned without duplicating rows in the DB.
        query = query.filter(
            db.or_(
                QuestionPaper.department == department,
                QuestionPaper.department == 'All Departments',
            )
        )
    if semester:
        query = query.filter(QuestionPaper.semester == semester)
    if class_name:
        query = query.filter(QuestionPaper.class_name == class_name)

    papers = query.order_by(QuestionPaper.year.desc(), QuestionPaper.subject_name).all()
    return jsonify([p.to_dict() for p in papers])


@main_bp.route('/api/papers/bookmarks', methods=['POST'])
@csrf.exempt
def api_papers_bookmarks():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify([])
    papers = QuestionPaper.query.filter(
        QuestionPaper.id.in_(ids),
        db.or_(
            QuestionPaper.status == 'approved',
            QuestionPaper.status.is_(None)
        )
    ).all()
    return jsonify([p.to_dict() for p in papers])


# ── Serve / download PDFs ──────────────────────────────────────────────────────
@main_bp.route('/view/<int:paper_id>')
def view_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], paper.filename)
    if not os.path.isfile(file_path):
        abort(404)
    try:
        paper.download_count = (paper.download_count or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()
    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'],
        paper.filename,
        mimetype='application/pdf',
        as_attachment=False,
    )


@main_bp.route('/download/<int:paper_id>')
def download_paper(paper_id):
    paper = QuestionPaper.query.get_or_404(paper_id)
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], paper.filename)
    if not os.path.isfile(file_path):
        abort(404)
    try:
        paper.download_count = (paper.download_count or 0) + 1
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Log download if student is logged in
    student_id = session.get('student_id')
    if student_id:
        try:
            from models import StudentDownload
            existing = StudentDownload.query.filter_by(student_id=student_id, paper_id=paper_id).first()
            if not existing:
                dl = StudentDownload(student_id=student_id, paper_id=paper_id)
                db.session.add(dl)
                db.session.commit()
        except Exception:
            db.session.rollback()

    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'],
        paper.filename,
        as_attachment=True,
        download_name=paper.original_name or paper.filename,
    )


# ── All papers page ────────────────────────────────────────────────────────────
@main_bp.route('/papers')
def papers():
    from sqlalchemy import func

    all_papers   = QuestionPaper.query.order_by(QuestionPaper.uploaded_at.desc()).all()
    total_papers = len(all_papers)
    total_depts  = db.session.query(func.count(QuestionPaper.department.distinct())).scalar() or 0
    year_min     = db.session.query(func.min(QuestionPaper.year)).scalar()
    year_max     = db.session.query(func.max(QuestionPaper.year)).scalar()
    year_range   = f"{year_min} – {year_max}" if year_min and year_max else "N/A"

    return render_template(
        'papers.html',
        papers=all_papers,
        total_papers=total_papers,
        total_depts=total_depts,
        year_range=year_range,
    )


# ── About page ─────────────────────────────────────────────────────────────────
@main_bp.route('/about')
def about():
    return render_template('about.html')


# ─────────────────────────────────────────────────────────────────────────────
#  AI Study Assistant Integration (Gemini API)
# ─────────────────────────────────────────────────────────────────────────────

def query_gemini(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("API Key is missing")
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            text = res_data['candidates'][0]['content']['parts'][0]['text']
            return text
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        print(f"Gemini API HTTP Error: {error_msg}")
        raise ValueError(f"HTTP Error {e.code}: {e.reason}")
    except Exception as e:
        print(f"Gemini API Error: {e}")
        raise e


def get_mock_ai_response(action: str, paper: QuestionPaper, message: str, rate_limited: bool = False) -> str:
    notice_str = (
        "> ⚠️ **Notice**: Live AI is currently rate-limited or temporarily exhausted (Gemini free tier has a limit of 20 requests per day). Showing cached/simulated revision guide."
        if rate_limited else
        "> ⚠️ **Notice**: Running in preview mode. Set the `GEMINI_API_KEY` environment variable in your `.env` file for real AI generation."
    )
    
    if action == 'revision':
        return f"""# 📚 AI Revision Guide: {paper.subject_name} ({paper.subject_code})
        
{notice_str}

## 1. Key Concepts & Core Topics
Based on the subject code **{paper.subject_code}** and subject **{paper.subject_name}**, here are the high-yield units:
- **Unit I: Fundamentals**: Core definitions, basic terms, and architectures.
- **Unit II: Design Principles**: Methods, models, and paradigms.
- **Unit III: Advanced Applications**: Performance analysis and integrations.

## 2. Expected Important Questions
1. Detail the architectural differences and major components.
2. Explain the workflow of key processes and algorithms with diagrams.
3. Discuss security, optimization, and future trends in this domain.

## 3. High-Yield Revision Tips
- Practice writing key definitions exactly as per textbook standards.
- Prepare schematics/flowcharts; drawing clean diagrams scores high in semester evaluations.
- Focus on past 3-year questions as patterns repeat up to 60%.
"""
    elif action == 'answers':
        return f"""# ✍️ Model Answers: {paper.subject_name}
        
{notice_str}

### Question 1: Explain the primary components of {paper.subject_name} systems.
**Answer:**
{paper.subject_name} systems are composed of three primary layers:
1. **Presentation Layer**: The user interface that handles request presentation.
2. **Business Logic Layer**: The core logic executing computations.
3. **Data Access Layer**: The database connector managing persistence.

### Question 2: Elaborate on the significance of the code {paper.subject_code}.
**Answer:**
The subject code **{paper.subject_code}** refers to the curriculum design guidelines. It signifies:
- **Regulation compliance**: Adapts to modernized syllabus requirements.
- **Scope**: Combines theoretical foundation with laboratory execution.
"""
    elif action == 'quiz':
        return f"""# 📝 Interactive Mock Quiz: {paper.subject_name}
        
{notice_str}

### Q1: What is the main objective of {paper.subject_name}?
- [ ] A) Reducing database storage size
- [ ] B) Providing systematic design and analysis of the subject domain
- [ ] C) Visualizing simple user interfaces only
- [ ] D) Running compilers and loaders

<details><summary><b>Reveal Answer</b></summary>
<b>Correct Option: B</b>
<br><i>Explanation:</i> {paper.subject_name} provides the formal foundations and practical methodologies for resolving problem statements in this discipline.
</details>

### Q2: Which semester does subject {paper.subject_code} belong to?
- [ ] A) 1st Semester
- [ ] B) 3rd Semester
- [ ] C) {paper.semester} Semester
- [ ] D) 8th Semester

<details><summary><b>Reveal Answer</b></summary>
<b>Correct Option: C</b>
<br><i>Explanation:</i> According to current college records, {paper.subject_code} is listed under the {paper.semester} Semester curriculum.
</details>
"""
    else: # chat
        if rate_limited:
            return f"""**AI Tutor**: Hello! I am your AI Study assistant for **{paper.subject_name}**.

⚠️ **Notice**: My live AI generation rate-limit was exceeded just now. I am answering using a cached response context.

To study the details, you asked: *"{message}"*
This topic is essential for **{paper.subject_code}** and is commonly asked as a 10-mark question! Let me know if you need help with anything else."""
        else:
            return f"""**AI Tutor**: Hello! I am your AI Study assistant for **{paper.subject_name}**.

⚠️ **Notice**: Running in preview mode because no `GEMINI_API_KEY` was found in the environment.

In the meantime, you asked: *"{message}"*
As a tutor, I can tell you that this topic is essential for **{paper.subject_code}** and is commonly asked as a 10-mark question! Let me know if you need help with anything else."""


@main_bp.route('/api/ai/chat/history/<int:paper_id>', methods=['GET'])
def ai_chat_history(paper_id):
    student_id = session.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'You must be logged in to view chat history.'}), 401

    chats = StudentAIChat.query.filter_by(student_id=student_id, paper_id=paper_id)\
        .order_by(StudentAIChat.created_at.asc()).all()

    return jsonify({
        'success': True,
        'history': [c.to_dict() for c in chats]
    })


@main_bp.route('/api/ai/study-guide', methods=['POST'])
@csrf.exempt
def ai_study_guide():
    data = request.get_json() or {}
    paper_id = data.get('paper_id')
    action = data.get('action') # 'revision', 'answers', 'quiz', 'chat'
    user_message = data.get('message', '').strip()
    
    if not paper_id or not action:
        return jsonify({'success': False, 'error': 'Missing paper_id or action'}), 400
        
    paper = QuestionPaper.query.get_or_404(paper_id)

    student_id = session.get('student_id')
    student = None
    if student_id:
        student = Student.query.get(student_id)

    # Save student message if chat
    if action == 'chat' and student and user_message:
        student_chat = StudentAIChat(
            student_id=student.id,
            paper_id=paper.id,
            sender='student',
            message=user_message
        )
        db.session.add(student_chat)
        db.session.commit()
    
    # Extract context text from PDF if it exists
    pdf_text = ""
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], paper.filename)
    if os.path.isfile(file_path):
        try:
            with pdfplumber.open(file_path) as pdf:
                pages_to_read = pdf.pages[:3]
                for page in pages_to_read:
                    text = page.extract_text()
                    if text:
                        pdf_text += "\n" + text
        except Exception as e:
            print(f"Error extracting PDF context: {e}")
            
    pdf_text = pdf_text.strip()
    
    context_desc = (
        f"Subject Code: {paper.subject_code}\n"
        f"Subject Name: {paper.subject_name}\n"
        f"Department: {paper.department}\n"
        f"Semester: {paper.semester}\n"
        f"Year of Exam: {paper.year}\n"
    )
    
    if pdf_text and len(pdf_text) > 100:
        context = f"{context_desc}\nHere is some text content extracted from the exam paper:\n{pdf_text[:8000]}"
    else:
        context = f"{context_desc}\nNote: This paper is a scanned image, so no direct text was extracted. Please use the metadata and subject details above to answer."

    # Formulate prompts
    if action == 'revision':
        prompt = (
            f"You are a helpful college professor. Based on this exam paper details:\n\n{context}\n\n"
            f"Provide a structured, easy-to-read Revision Study Guide for this subject. Include:\n"
            f"1. Core Concepts & Definitions (based on subject name/code and questions if available).\n"
            f"2. High-Yield/Important Topics that students should focus on.\n"
            f"3. Revision tips.\n"
            f"Use markdown headers, lists, and bold text. Keep it concise, engaging, and extremely helpful for a student revising 1 day before the exam."
        )
    elif action == 'answers':
        prompt = (
            f"You are a college teacher. Based on this exam paper details:\n\n{context}\n\n"
            f"Select 3 important/complex questions or topics that are typical for this subject ({paper.subject_name}). "
            f"Provide clear, model answers or step-by-step solutions/explanations for them so students can study how to answer them in the semester exam.\n"
            f"Use markdown formatting and organize it cleanly."
        )
    elif action == 'quiz':
        prompt = (
            f"You are an examiner. Based on this exam details:\n\n{context}\n\n"
            f"Generate a mock practice quiz containing 5 multiple-choice questions (MCQs) for the subject '{paper.subject_name}'.\n"
            f"Format it beautifully using markdown. For each question, provide 4 options (A, B, C, D) and then show the correct answer and a brief 1-sentence explanation hidden inside a collapsible details tag like this:\n"
            f"<details><summary><b>Reveal Answer</b></summary>Correct Option: A. Explanation: ...</details>\n"
            f"This will allow students to self-test interactively on the webpage."
        )
    elif action == 'chat':
        if not user_message:
            return jsonify({'success': False, 'error': 'Message is required for chat'}), 400
        prompt = (
            f"You are a friendly academic AI Tutor for this subject:\n\n{context}\n\n"
            f"A student is asking you a question about this subject or paper. Please answer clearly, accurately, and helpful like a great tutor.\n"
            f"Student Question: '{user_message}'"
        )
    else:
        return jsonify({'success': False, 'error': 'Invalid action'}), 400

    # Execute query
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        mock_response = get_mock_ai_response(action, paper, user_message)
        if action == 'chat' and student and mock_response:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=mock_response
            )
            db.session.add(tutor_chat)
            db.session.commit()
        return jsonify({
            'success': True, 
            'response': mock_response,
            'is_mock': True,
            'notice': 'To enable live AI answers, set the GEMINI_API_KEY environment variable.'
        })
        
    try:
        response_text = query_gemini(prompt)
        if action == 'chat' and student and response_text:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=response_text
            )
            db.session.add(tutor_chat)
            db.session.commit()
        return jsonify({
            'success': True,
            'response': response_text,
            'is_mock': False
        })
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini API execution error: {error_msg}")
        mock_response = get_mock_ai_response(action, paper, user_message, rate_limited=True)
        if action == 'chat' and student and mock_response:
            tutor_chat = StudentAIChat(
                student_id=student.id,
                paper_id=paper.id,
                sender='tutor',
                message=mock_response
            )
            db.session.add(tutor_chat)
            db.session.commit()
        return jsonify({
            'success': True,
            'response': mock_response,
            'is_mock': True,
            'notice': f"Live AI rate-limited or unavailable ({error_msg}). Loaded offline guide."
        })


# ─────────────────────────────────────────────────────────────────────────────
#  Student/Staff Public Contribution Routes
# ─────────────────────────────────────────────────────────────────────────────

from routes.admin import parse_pdf_metadata
from werkzeug.utils import secure_filename
import uuid

@main_bp.route('/api/parse-pdf-public', methods=['POST'])
@csrf.exempt
def api_parse_pdf_public():
    if 'pdf_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
        
    try:
        file.seek(0)
        file_bytes = file.read()
        file_stream = io.BytesIO(file_bytes)
        
        metadata = parse_pdf_metadata(file_stream, file.filename)
        return jsonify({'success': True, 'metadata': metadata})
    except Exception as e:
        print(f"Public PDF parser error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/contribute', methods=['GET', 'POST'])
@csrf.exempt
def contribute():
    if not session.get('student_logged_in'):
        if request.method == 'POST':
            return jsonify({'success': False, 'error': 'Please log in to contribute papers.'}), 401
        flash('Please log in to access this page.', 'warning')
        return redirect('/student/login?force_student=true')

    if request.method == 'POST':
        pdf_file = request.files.get('pdf_file')
        department = request.form.get('department', '').strip()
        semester = request.form.get('semester', '').strip()
        subject_code = request.form.get('subject_code', '').strip().upper()
        subject_name = request.form.get('subject_name', '').strip()
        exam_type = request.form.get('exam_type', '').strip()
        year_str = request.form.get('year', '').strip()
        class_name = request.form.get('class_name', '').strip()
        
        if not (pdf_file and department and semester and subject_code and subject_name and exam_type and year_str):
            return jsonify({'success': False, 'error': 'All fields are required!'}), 400
            
        try:
            year = int(year_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid year format!'}), 400
            
        orig_name = pdf_file.filename
        ext = os.path.splitext(orig_name)[1].lower()
        if ext != '.pdf':
            return jsonify({'success': False, 'error': 'Only PDF files are allowed!'}), 400
            
        unique_fn = f"{uuid.uuid4().hex}_{secure_filename(orig_name)}"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_fn)
        pdf_file.save(file_path)
        
        paper = QuestionPaper(
            department=department,
            semester=semester,
            subject_code=subject_code,
            subject_name=subject_name,
            exam_type=exam_type,
            year=year,
            class_name=class_name or None,
            filename=unique_fn,
            original_name=orig_name,
            status='pending'
        )
        db.session.add(paper)
        db.session.flush() # Populate paper.id before commit
        
        # Add admin notification
        notif = AdminNotification(
            message=f"New paper contribution: {paper.subject_code} - {paper.subject_name} for {paper.department} (Semester {paper.semester})"
        )
        db.session.add(notif)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Paper submitted successfully! It will be live after Admin review.',
            'paper_id': paper.id,
            'subject_code': paper.subject_code,
            'subject_name': paper.subject_name
        })
        
    departments = db.session.query(QuestionPaper.department).distinct().order_by(QuestionPaper.department).all()
    depts = [d[0] for d in departments if d[0] and d[0] not in ['Select Department', 'All Departments', 'all']]
    if not depts:
        depts = ['B.Sc Computer Science', 'BCA', 'B.Sc Information Technology', 'B.Com General', 'BBA', 'B.A English Literature']
        
    return render_template('contribute.html', departments=depts)


@main_bp.route('/api/papers/contributions/status', methods=['POST'])
@csrf.exempt
def api_contributions_status():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    if not ids:
        return jsonify([])
        
    try:
        int_ids = [int(i) for i in ids]
    except (ValueError, TypeError):
        return jsonify([])
        
    papers = QuestionPaper.query.filter(QuestionPaper.id.in_(int_ids)).all()
    found_ids = {p.id: p for p in papers}
    
    results = []
    for pid in int_ids:
        if pid in found_ids:
            paper = found_ids[pid]
            results.append({
                'id': pid,
                'status': paper.status or 'approved',
                'subject_code': paper.subject_code,
                'subject_name': paper.subject_name
            })
        else:
            results.append({
                'id': pid,
                'status': 'rejected'
            })
            
    return jsonify(results)


# ─────────────────────────────────────────────────────────────────────────────
#  Public Question Discussion Forum Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@main_bp.route('/api/papers/<int:paper_id>/discussion', methods=['GET'])
def get_discussion(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    posts = DiscussionPost.query.filter_by(paper_id=paper_id).order_by(DiscussionPost.created_at.asc()).all()
    serialized = [p.to_dict() for p in posts]
    return jsonify(serialized)


@main_bp.route('/api/papers/<int:paper_id>/discussion', methods=['POST'])
@csrf.exempt
def post_discussion(paper_id):
    QuestionPaper.query.get_or_404(paper_id)
    
    data = request.get_json() or {}
    author_name = data.get('author_name', '').strip() or 'Anonymous Student'
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')
    
    if not content:
        return jsonify({'success': False, 'error': 'Comment content cannot be empty!'}), 400
        
    post = DiscussionPost(
        paper_id=paper_id,
        author_name=author_name,
        content=content,
        parent_id=parent_id
    )
    db.session.add(post)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Comment posted successfully!',
        'post': post.to_dict()
    })