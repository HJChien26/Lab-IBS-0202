from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import json

app = Flask(__name__)
app.secret_key = 'lab_secret_key_123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///lab_res.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 資料庫模型 ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class FreezerBox(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    box_name = db.Column(db.String(50))
    user_name = db.Column(db.String(50))
    start_date = db.Column(db.Date)

class BSCReservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bsc_id = db.Column(db.Integer)
    date = db.Column(db.Date)
    time_slot = db.Column(db.Integer)
    user_name = db.Column(db.String(50))

class IHCReservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date)
    time_slot = db.Column(db.String(20))
    trays = db.Column(db.Integer)
    user_name = db.Column(db.String(50))

with app.app_context():
    db.create_all()

# --- 全域變數注入 ---
@app.context_processor
def inject_users():
    return dict(users=User.query.all(), current_user=session.get('user'))

# --- 核心路由 ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/set_user', methods=['POST'])
def set_user():
    session['user'] = request.form.get('user_name')
    return redirect(request.referrer or url_for('index'))

@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    if request.method == 'POST':
        if 'add_user' in request.form:
            name = request.form.get('new_name', '').strip()
            # 限制 10 字元
            if name and len(name) <= 10:
                if not User.query.filter_by(name=name).first():
                    db.session.add(User(name=name))
                    db.session.commit()
        elif 'del_user' in request.form:
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user:
                db.session.delete(user)
                db.session.commit()
    return render_template('manage_users.html')

# --- BSC 預約系統 ---

@app.route('/bsc')
def bsc():
    today = datetime.now().date()
    end_date = today + timedelta(days=13)
    dates = [today + timedelta(days=i) for i in range(14)]
    all_res = BSCReservation.query.filter(BSCReservation.date >= (today - timedelta(days=1))).all()
    booked = {i: {} for i in range(1, 5)}
    for r in all_res:
        if r.bsc_id in booked:
            booked[r.bsc_id][(r.date, r.time_slot)] = r.user_name
    return render_template('bsc.html', dates=dates, booked=booked, today=today, end_date=end_date)

@app.route('/bsc_batch', methods=['POST'])
def bsc_batch():
    if not session.get('user'):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json.get('items', [])
    current_user = session['user']
    for item in data:
        target_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
        bsc_id, slot, mode = int(item['bsc']), int(item['slot']), item['mode']
        if mode == 'reserve':
            if not BSCReservation.query.filter_by(bsc_id=bsc_id, date=target_date, time_slot=slot).first():
                db.session.add(BSCReservation(bsc_id=bsc_id, date=target_date, time_slot=slot, user_name=current_user))
        elif mode == 'cancel':
            res = BSCReservation.query.filter_by(bsc_id=bsc_id, date=target_date, time_slot=slot, user_name=current_user).first()
            if res:
                db.session.delete(res)
    db.session.commit()
    return jsonify({"status": "success"})

# --- Freezer 漸凍盒系統 ---

@app.route('/freezer', methods=['GET', 'POST'])
def freezer():
    today = datetime.now().date()
    if request.method == 'POST':
        # 支援逗號批量新增
        if 'add_box' in request.form:
            raw_input = request.form.get('box_name', '')
            # 支援半形與全形逗號切割
            names = [n.strip() for n in raw_input.replace('，', ',').split(',') if n.strip()]
            for name in names:
                if not FreezerBox.query.filter_by(box_name=name).first():
                    db.session.add(FreezerBox(box_name=name))
            db.session.commit()
            
        elif 'use_box' in request.form and session.get('user'):
            box = FreezerBox.query.get(request.form.get('box_id'))
            if box:
                box.user_name = session['user']
                box.start_date = today
                db.session.commit()
                
        elif 'return_box' in request.form:
            box = FreezerBox.query.get(request.form.get('box_id'))
            if box:
                box.user_name = box.start_date = None
                db.session.commit()
                
        elif 'delete_box' in request.form:
            box = FreezerBox.query.get(request.form.get('box_id'))
            # 僅限非使用中才能刪除
            if box and not box.user_name:
                db.session.delete(box)
                db.session.commit()
                
        return redirect(url_for('freezer'))

    # 處理天數計算與排序
    all_boxes = FreezerBox.query.all()
    in_use, available = [], []
    for b in all_boxes:
        if b.user_name:
            b.days_used = (today - b.start_date).days
            b.overdue_days = b.days_used - 7 if b.days_used > 7 else 0
            # 逾期 7 天以上優先排序 (負號越小排越前)
            b.priority = -b.days_used if b.days_used >= 7 else 0
            in_use.append(b)
        else:
            available.append(b)

    in_use.sort(key=lambda x: (x.priority, x.box_name))
    available.sort(key=lambda x: x.box_name)

    return render_template('freezer.html', in_use_boxes=in_use, available_boxes=available, today=today)

# --- IHC 染色系統 ---

@app.route('/ihc', methods=['GET', 'POST'])
def ihc():
    today = datetime.now().date()
    if request.method == 'POST' and session.get('user'):
        slot, trays = request.form.get('slot'), int(request.form.get('trays'))
        existing = db.session.query(db.func.sum(IHCReservation.trays)).filter_by(date=today, time_slot=slot).scalar() or 0
        # 每日單一時段限制 3 盤
        if existing + trays <= 3:
            db.session.add(IHCReservation(date=today, time_slot=slot, trays=trays, user_name=session['user']))
            db.session.commit()
        return redirect(url_for('ihc'))
    
    slots = ['AM1', 'AM2', 'AM3', 'PM1', 'PM2', 'PM3']
    usage = {s: (db.session.query(db.func.sum(IHCReservation.trays)).filter_by(date=today, time_slot=s).scalar() or 0) for s in slots}
    return render_template('ihc.html', today=today, usage=usage, slots=slots)

if __name__ == '__main__':
    app.run(debug=True)