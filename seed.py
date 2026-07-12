"""
seed.py — Run once to populate the DB with sample records.
Usage:  python seed.py
"""
from app import create_app
from extensions import db
from models import QuestionPaper, AdminUser, Badge    # <-- Ingu AdminUser nu mathiyachu
from datetime import datetime

SAMPLE_DATA = [
    dict(department='CSE', semester='3rd', subject_code='CS3352', subject_name='Foundations of Data Science',    exam_type='Model Exam',         year=2024, filename='sample.pdf', original_name='CS3352_ModelExam_2024.pdf'),
    dict(department='CSE', semester='3rd', subject_code='CS3391', subject_name='Object Oriented Programming',   exam_type='University Semester', year=2024, filename='sample.pdf', original_name='CS3391_UniSem_2024.pdf'),
    dict(department='CSE', semester='5th', subject_code='CS3501', subject_name='Theory of Computation',         exam_type='Model Exam',         year=2023, filename='sample.pdf', original_name='CS3501_ModelExam_2023.pdf'),
    dict(department='ECE', semester='4th', subject_code='EC3401', subject_name='Signals and Systems',           exam_type='University Semester', year=2023, filename='sample.pdf', original_name='EC3401_UniSem_2023.pdf'),
    dict(department='ECE', semester='2nd', subject_code='EC3201', subject_name='Electronic Devices & Circuits', exam_type='Model Exam',         year=2022, filename='sample.pdf', original_name='EC3201_ModelExam_2022.pdf'),
    dict(department='Mech',semester='6th', subject_code='ME3601', subject_name='Heat and Mass Transfer',        exam_type='University Semester', year=2022, filename='sample.pdf', original_name='ME3601_UniSem_2022.pdf'),
    dict(department='Civil',semester='4th', subject_code='CE3401', subject_name='Structural Analysis',          exam_type='Model Exam',         year=2021, filename='sample.pdf', original_name='CE3401_ModelExam_2021.pdf'),
    dict(department='IT',  semester='5th', subject_code='IT3501', subject_name='Web Technology',               exam_type='University Semester', year=2021, filename='sample.pdf', original_name='IT3501_UniSem_2021.pdf'),
]

def seed():
    app = create_app()
    with app.app_context():
        db.create_all()

        # ── 1. Secure Admin Account Creation ──────────────────────────────
        admin_user = AdminUser.query.filter_by(username='admin').first()

        if not admin_user:
            new_admin = AdminUser(username='admin')
            new_admin.set_password('manivig@12') # Unga model-la ulla function!
            db.session.add(new_admin)
            print('✅ Admin account successfully created with secure password!')
        else:
            admin_user.set_password('manivig@12')
            print('✅ Admin password updated to secure hashed version!')
            
        db.session.commit()

        # ── 2. Question Papers Setup ─────────────────────────────────────
        if QuestionPaper.query.count() == 0:
            for item in SAMPLE_DATA:
                db.session.add(QuestionPaper(**item))
            db.session.commit()
            print(f'✅ Seeded {len(SAMPLE_DATA)} sample records.')
        else:
            print('ℹ️  Question papers already exist. Skipping paper seed.')

        # ── 3. Default Badges Setup ──────────────────────────────────────
        DEFAULT_BADGES = [
            dict(name='First Steps',  description='Registered a student account',           icon='🌱', threshold=0,   category='general'),
            dict(name='Contributor',  description='Uploaded 1 approved question paper',      icon='📤', threshold=1,   category='contribution'),
            dict(name='Scholar',      description='Saved 10 question papers',                icon='📚', threshold=10,  category='study'),
            dict(name='Debater',      description='Posted 10 discussion comments',           icon='💬', threshold=10,  category='discussion'),
            dict(name='Achiever',     description='Earned 100 points on the platform',       icon='🏆', threshold=100, category='general'),
            dict(name='Legend',       description='Earned 500 points — true dedication!',    icon='👑', threshold=500, category='general'),
        ]

        if Badge.query.count() == 0:
            for b in DEFAULT_BADGES:
                db.session.add(Badge(**b))
            db.session.commit()
            print(f'✅ Seeded {len(DEFAULT_BADGES)} default badges.')
        else:
            print('ℹ️  Badges already exist. Skipping badge seed.')

if __name__ == '__main__':
    seed()