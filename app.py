from flask import Flask, session, redirect, url_for, request
from extensions import db, csrf
from routes.main import main_bp
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.student_auth import student_auth_bp
from routes.student import student_bp
from routes.moderator import moderator_bp
import os
from dotenv import load_dotenv

load_dotenv()

def create_app():
    app = Flask(__name__)

    # ── Configuration ──────────────────────────────────────────────
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///questionbank.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # BUG FIX 1: Changed 'static/uploads/pdfs' to 'static/uploads'
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
    
    app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024   # 50 MB cap

    # Folder illana automatic ah create pannidum
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # ── Extensions ─────────────────────────────────────────────────
    db.init_app(app)
    csrf.init_app(app)

    # ── Step 1: attach hooks to blueprints BEFORE registering them ─
    @admin_bp.before_request
    def require_admin_login():
        # BUG FIX 2: Check endpoint to prevent infinite redirect loops
        if not session.get('admin_logged_in') and request.endpoint != 'auth.login':
            return redirect(url_for('auth.login', next=request.url))
        # Staff moderators must use /moderator — block them from /admin
        if session.get('admin_logged_in') and session.get('admin_role') == 'staff':
            return redirect('/moderator')

    # ── Step 2: register blueprints AFTER all hooks are defined ────
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(auth_bp)            # provides /login and /logout
    app.register_blueprint(student_auth_bp)    # provides /student/login, /student/register, /student/logout
    app.register_blueprint(student_bp)         # provides /student/dashboard, /leaderboard, /student/planner
    app.register_blueprint(moderator_bp, url_prefix='/moderator')  # Staff moderator portal

    # ── DB init ────────────────────────────────────────────────────
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text("ALTER TABLE question_papers ADD COLUMN download_count INTEGER DEFAULT 0"))
                conn.commit()
        except Exception:
            pass

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)